import json

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from .analysis import analysis_payload, prepare_analysis_request, prepare_follow_up_request
from .discovery import discover_repository
from .errors import DiscoveryError
from .github_client import GitHubClient
from .retrieval import retrieve_repository_sources, stream_retrieve_follow_up_sources, stream_retrieve_repository_sources
from .analysis import analyze_retrieval
from .providers import get_analysis_provider
from .providers.base import AnalysisResponse

api = Blueprint("api", __name__, url_prefix="/api")


def error_response(error: DiscoveryError):
    return jsonify({"error": {"code": error.code, "message": error.message}}), error.status_code


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def source_file_payload(retrieval: dict) -> list[dict]:
    return [
        {key: file[key] for key in ("path", "language", "content", "size", "commit_sha")}
        for file in retrieval["selected_files"]
    ]


def selected_file_payload(retrieval: dict) -> list[dict]:
    return [
        {key: file[key] for key in ("path", "language", "size", "status")}
        for file in retrieval["selected_files"]
    ]


@api.get("/health")
def health():
    return jsonify({"ok": True, "service": "repopilot-discovery", "github_token_configured": bool(current_app.config.get("GITHUB_TOKEN"))})


@api.post("/discover")
def discover():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response(DiscoveryError("invalid_request", "Send a JSON object containing a repository URL."))
    try:
        factory = current_app.config.get("GITHUB_CLIENT_FACTORY", GitHubClient)
        return jsonify(discover_repository(payload.get("url"), factory()))
    except DiscoveryError as error:
        return error_response(error)
    except Exception:
        current_app.logger.exception("Unexpected repository discovery failure")
        return error_response(DiscoveryError("server_error", "RepoPilot could not complete discovery. Please retry.", 500))


@api.post("/retrieve")
def retrieve():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response(DiscoveryError("invalid_request", "Send a JSON object containing a repository URL."))
    try:
        factory = current_app.config.get("GITHUB_CLIENT_FACTORY", GitHubClient)
        return jsonify(retrieve_repository_sources(payload.get("url"), payload.get("instruction"), factory(), payload.get("mode", "overview")))
    except DiscoveryError as error:
        return error_response(error)
    except Exception:
        current_app.logger.exception("Unexpected source retrieval failure")
        return error_response(DiscoveryError("server_error", "RepoPilot could not retrieve source files. Please retry.", 500))


@api.post("/analyze")
def analyze():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response(DiscoveryError("invalid_request", "Send a JSON object containing a repository URL."))
    try:
        github_factory = current_app.config.get("GITHUB_CLIENT_FACTORY", GitHubClient)
        provider_factory = current_app.config.get("ANALYSIS_PROVIDER_FACTORY", get_analysis_provider)
        retrieval = retrieve_repository_sources(payload.get("url"), payload.get("instruction"), github_factory(), payload.get("mode", "overview"))
        return jsonify(analyze_retrieval(retrieval, provider_factory()))
    except DiscoveryError as error:
        return error_response(error)
    except Exception:
        current_app.logger.exception("Unexpected repository analysis failure")
        return error_response(DiscoveryError("server_error", "RepoPilot could not complete analysis. Please retry.", 500))


@api.post("/analyze/stream")
def analyze_stream():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response(DiscoveryError("invalid_request", "Send a JSON object containing a repository URL."))
    url = payload.get("url")
    instruction = payload.get("instruction")
    mode = payload.get("mode", "overview")

    @stream_with_context
    def generate():
        try:
            github_factory = current_app.config.get("GITHUB_CLIENT_FACTORY", GitHubClient)
            steps = stream_retrieve_repository_sources(url, instruction, github_factory(), mode)
            while True:
                try:
                    update = next(steps)
                    yield sse(update["event"], update["data"])
                except StopIteration as complete:
                    retrieval = complete.value
                    break

            conversation_cache = current_app.extensions["repopilot_conversations"]
            conversation_id = conversation_cache.store(retrieval, payload.get("conversation_id"))

            provider_factory = current_app.config.get("ANALYSIS_PROVIDER_FACTORY", get_analysis_provider)
            provider = provider_factory()
            request_data = prepare_analysis_request(retrieval)
            yield sse("status", {"stage": "analysis", "message": "Preparing source-grounded analysis…"})
            answer = ""
            stream_method = getattr(provider, "stream_analyze", None)
            if callable(stream_method):
                for token in stream_method(request_data):
                    answer += token
                    yield sse("token", {"text": token})
            else:
                # Compatibility path for a future provider that has not implemented streaming yet.
                response = provider.analyze(request_data)
                answer = response.text
                yield sse("token", {"text": answer})
            if not answer.strip():
                raise DiscoveryError("analysis_failed", "The analysis ended without a response. Please retry.", 502)
            analysis = analysis_payload(request_data, AnalysisResponse(
                text=answer,
                provider=getattr(provider, "name", "configured-provider"),
                model=getattr(provider, "model", "configured-model"),
            ))
            # These are the exact bounded files supplied to the analysis provider. Including them
            # lets the browser open only verified, retrieved file references without
            # fetching a different revision after the analysis completes.
            yield sse("done", {
                "analysis": analysis,
                "retrieval_coverage": retrieval["retrieval_coverage"],
                "selected_files": selected_file_payload(retrieval),
                "source_files": source_file_payload(retrieval),
                "conversation_id": conversation_id,
            })
        except DiscoveryError as error:
            yield sse("error", {"code": error.code, "message": error.message})
        except GeneratorExit:
            return
        except Exception:
            current_app.logger.exception("Unexpected streaming analysis failure")
            yield sse("error", {"code": "server_error", "message": "RepoPilot could not complete analysis. Please retry."})

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@api.post("/conversations/follow-up/stream")
def follow_up_stream():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response(DiscoveryError("invalid_request", "Send a JSON object containing a follow-up question."))
    question = str(payload.get("question") or "").strip()
    if not question:
        return error_response(DiscoveryError("invalid_request", "Enter a follow-up question."))

    @stream_with_context
    def generate():
        try:
            conversation_cache = current_app.extensions["repopilot_conversations"]
            conversation_id = payload.get("conversation_id")
            previous = conversation_cache.retrieval(conversation_id)
            github_factory = current_app.config.get("GITHUB_CLIENT_FACTORY", GitHubClient)
            steps = stream_retrieve_follow_up_sources(previous, question, github_factory())
            while True:
                try:
                    update = next(steps)
                    yield sse(update["event"], update["data"])
                except StopIteration as complete:
                    retrieval = complete.value
                    break

            conversation_cache.merge_sources(conversation_id, retrieval)
            provider_factory = current_app.config.get("ANALYSIS_PROVIDER_FACTORY", get_analysis_provider)
            provider = provider_factory()
            request_data = prepare_follow_up_request(retrieval, question, payload.get("history"))
            yield sse("status", {"stage": "analysis", "message": "Preparing source-grounded follow-up…"})
            answer = ""
            stream_method = getattr(provider, "stream_analyze", None)
            if callable(stream_method):
                for token in stream_method(request_data):
                    answer += token
                    yield sse("token", {"text": token})
            else:
                response = provider.analyze(request_data)
                answer = response.text
                yield sse("token", {"text": answer})
            if not answer.strip():
                raise DiscoveryError("analysis_failed", "The follow-up analysis ended without a response. Please retry.", 502)
            analysis = analysis_payload(request_data, AnalysisResponse(
                text=answer,
                provider=getattr(provider, "name", "configured-provider"),
                model=getattr(provider, "model", "configured-model"),
            ))
            yield sse("done", {
                "analysis": analysis,
                "retrieval_coverage": retrieval["retrieval_coverage"],
                "selected_files": selected_file_payload(retrieval),
                "source_files": source_file_payload(retrieval),
                "conversation_id": conversation_id,
            })
        except DiscoveryError as error:
            yield sse("error", {"code": error.code, "message": error.message})
        except GeneratorExit:
            return
        except Exception:
            current_app.logger.exception("Unexpected follow-up analysis failure")
            yield sse("error", {"code": "server_error", "message": "RepoPilot could not complete this follow-up. Please retry."})

    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
