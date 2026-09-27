import base64
import json
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from backend.discovery import calculate_language_statistics, discover_repository, parse_tree
from backend.errors import DiscoveryError
from backend.retrieval import retrieve_repository_sources, select_files
from backend.analysis import analysis_payload, analyze_retrieval, load_analysis_instructions, parse_architecture_data, prepare_analysis_request
from backend.analysis import prepare_follow_up_request
from backend.conversations import ConversationCache
from backend.retrieval import stream_retrieve_follow_up_sources
from backend.providers.anthropic_provider import AnthropicProvider
from backend.providers.base import AnalysisResponse, ArchitectureComponent, ArchitectureConnection, ArchitectureData
from backend.providers.factory import get_analysis_provider
from backend.routes import sse
from backend.url_validation import parse_repository_url


class FakeGitHubClient:
    def repository(self, _identifier):
        return {
            "name": "demo", "full_name": "octo/demo", "html_url": "https://github.com/octo/demo",
            "owner": {"login": "octo", "html_url": "https://github.com/octo"}, "description": "Fixture repository",
            "language": "Python", "default_branch": "main", "visibility": "public", "archived": False,
            "stargazers_count": 7, "forks_count": 2, "updated_at": "2026-09-27T00:00:00Z",
            "license": {"spdx_id": "MIT"},
        }

    def languages(self, _identifier):
        return {"Python": 120, "Shell": 20}

    def latest_commit(self, _identifier, _branch):
        return {"sha": "a" * 40, "html_url": "https://github.com/octo/demo/commit/fixture"}

    def tree(self, _identifier, _sha):
        return {
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob", "mode": "100644", "size": 30, "sha": "r"},
                {"path": "pyproject.toml", "type": "blob", "mode": "100644", "size": 50, "sha": "p"},
                {"path": "src", "type": "tree", "mode": "040000", "sha": "s"},
                {"path": "src/main.py", "type": "blob", "mode": "100644", "size": 40, "sha": "m"},
                {"path": "tests", "type": "tree", "mode": "040000", "sha": "t"},
                {"path": "tests/test_main.py", "type": "blob", "mode": "100644", "size": 35, "sha": "x"},
                {"path": "link", "type": "blob", "mode": "120000", "size": 4, "sha": "l"},
            ],
        }

    def file_content(self, _identifier, path, commit_sha):
        contents = {
            "README.md": b"# Demo\n",
            "pyproject.toml": b"[project]\nname = 'demo'\n",
            "src/main.py": b"def main():\n    return 'fixture'\n",
            "tests/test_main.py": b"def test_main():\n    assert True\n",
        }
        raw = contents.get(path)
        if raw is None:
            return None
        return {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii"), "sha": commit_sha}


class UnavailableGitHubClient:
    def repository(self, _identifier):
        raise DiscoveryError("repository_unavailable", "This repository was not found or is not publicly accessible.", 404)


class UnavailableContentClient(FakeGitHubClient):
    def file_content(self, _identifier, path, _commit_sha):
        return None if path == "src/main.py" else super().file_content(_identifier, path, _commit_sha)


class FakeAnalysisProvider:
    def analyze(self, _request):
        return AnalysisResponse(text="## Overview\nVerified fixture analysis citing `src/main.py`.", provider="fake", model="fixture-model")


class ContractFakeProvider:
    """Test-only stand-in proving RepoPilot owns modes and presentation data."""

    name = "test-only-provider"
    model = "test-model"

    def analyze(self, request):
        if request.mode == "architecture":
            return AnalysisResponse(
                text="Architecture from a test-only provider.", provider=self.name, model=self.model,
                architecture=ArchitectureData(
                    components=(ArchitectureComponent("entry", "Entry point", "cli", ("src/main.py",), "Verified entry"),),
                ),
            )
        if request.mode == "custom":
            return AnalysisResponse(text="`src/main.py` is the verified entry point.", provider=self.name, model=self.model)
        return AnalysisResponse(text="## Project summary\nFixture overview.\n## Main technologies\nPython.\n## Core components\n`src/main.py`.", provider=self.name, model=self.model)


class FakeStreamingProvider(FakeAnalysisProvider):
    name = "fake-stream"
    model = "fixture-stream-model"

    def stream_analyze(self, _request):
        yield "## Overview\n"
        yield "Verified fixture analysis citing `src/main.py`."


class FailingStreamingProvider(FakeStreamingProvider):
    def stream_analyze(self, _request):
        yield "Partial response"
        raise DiscoveryError("analysis_failed", "The analysis stream was interrupted.", 502)


class CapturingStreamingProvider(FakeStreamingProvider):
    requests = []

    def stream_analyze(self, request):
        type(self).requests.append(request)
        yield "Follow-up grounded in `src/main.py`."


class FollowUpGitHubClient(FakeGitHubClient):
    requested_refs = []

    def tree(self, _identifier, _sha):
        payload = super().tree(_identifier, _sha)
        payload["tree"].append({"path": "src/auth.py", "type": "blob", "mode": "100644", "size": 38, "sha": "a"})
        return payload

    def file_content(self, identifier, path, commit_sha):
        type(self).requested_refs.append(commit_sha)
        if path == "src/auth.py":
            return {"encoding": "base64", "content": base64.b64encode(b"def authenticate(token):\n    return bool(token)\n").decode("ascii")}
        return super().file_content(identifier, path, commit_sha)


class UrlValidationTests(unittest.TestCase):
    def test_normalizes_trailing_slash_and_git_suffix(self):
        identifier = parse_repository_url("https://www.github.com/facebook/react.git/")
        self.assertEqual((identifier.owner, identifier.name), ("facebook", "react"))
        self.assertEqual(identifier.canonical_url, "https://github.com/facebook/react")

    def test_rejects_untrusted_or_non_repository_urls(self):
        for value in ("http://github.com/a/b", "https://example.com/a/b", "https://github.com/a/b/issues", "file:///tmp/repo"):
            with self.subTest(value=value), self.assertRaises(DiscoveryError):
                parse_repository_url(value)


class TreeParsingTests(unittest.TestCase):
    def test_builds_tree_and_verified_characteristics(self):
        result = discover_repository("https://github.com/octo/demo", FakeGitHubClient())
        self.assertEqual(result["repository"]["latest_commit"]["sha"], "a" * 40)
        self.assertTrue(result["discovery_coverage"]["complete"])
        self.assertEqual(result["file_statistics"]["files"], 4)
        self.assertEqual(result["file_statistics"]["directories"], 2)
        self.assertEqual(result["file_statistics"]["symlinks"], 1)
        self.assertIn({"path": "README.md", "kind": "Repository documentation"}, result["characteristics"]["recognized_files"])
        self.assertEqual(result["characteristics"]["test_directories"], ["tests"])

    def test_marks_a_truncated_tree_partial(self):
        _tree, _statistics, details = parse_tree([{"path": "a.txt", "type": "blob", "mode": "100644", "size": 1}], True)
        self.assertFalse(details["coverage"]["complete"])
        self.assertTrue(details["coverage"]["truncated_by_github"])


class LanguageStatisticsTests(unittest.TestCase):
    def test_calculates_percentages_from_github_language_bytes(self):
        statistics = calculate_language_statistics({"TypeScript": 974, "JavaScript": 14, "CSS": 12})
        self.assertEqual(statistics["total_bytes"], 1000)
        self.assertEqual(
            statistics["languages"],
            [
                {"name": "TypeScript", "bytes": 974, "percentage": 97.4},
                {"name": "JavaScript", "bytes": 14, "percentage": 1.4},
                {"name": "CSS", "bytes": 12, "percentage": 1.2},
            ],
        )

    def test_handles_unknown_or_empty_languages(self):
        self.assertEqual(calculate_language_statistics({}), {"total_bytes": 0, "languages": []})
        self.assertEqual(calculate_language_statistics(None), {"total_bytes": 0, "languages": []})


class DiscoveryApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, GITHUB_CLIENT_FACTORY=FakeGitHubClient)
        self.client = self.app.test_client()

    def test_discover_returns_structured_real_client_data(self):
        response = self.client.post("/api/discover", json={"url": "https://github.com/octo/demo"})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["repository"]["full_name"], "octo/demo")
        self.assertIn("tree", payload)
        self.assertIn("discovery_coverage", payload)

    def test_invalid_url_returns_consistent_error(self):
        response = self.client.post("/api/discover", json={"url": "https://not-github.example/repo"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_url")

    def test_inaccessible_repository_returns_access_error(self):
        self.app.config["GITHUB_CLIENT_FACTORY"] = UnavailableGitHubClient
        response = self.client.post("/api/discover", json={"url": "https://github.com/octo/missing"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "repository_unavailable")


class SourceRetrievalTests(unittest.TestCase):
    def test_focus_terms_prioritize_matching_source_paths(self):
        entries = [
            {"path": "src/other.py", "type": "blob", "mode": "100644", "size": 10},
            {"path": "src/auth/session.py", "type": "blob", "mode": "100644", "size": 10},
        ]
        selected, skipped = select_files(entries, "Explain the authentication system")
        self.assertEqual(selected[0]["path"], "src/auth/session.py")
        self.assertIn("matches focus terms", " ".join(selected[0]["reasons"]))
        self.assertEqual(skipped, {})

    def test_excludes_sensitive_binary_and_large_files(self):
        entries = [
            {"path": ".env", "type": "blob", "mode": "100644", "size": 5},
            {"path": "keys/private.pem", "type": "blob", "mode": "100644", "size": 5},
            {"path": "config/production.key", "type": "blob", "mode": "100644", "size": 5},
            {"path": "image.png", "type": "blob", "mode": "100644", "size": 5},
            {"path": "big.py", "type": "blob", "mode": "100644", "size": 60_001},
        ]
        selected, skipped = select_files(entries)
        self.assertEqual(selected, [])
        self.assertEqual(skipped["sensitive_file"], 3)
        self.assertEqual(skipped["binary_or_generated"], 1)
        self.assertEqual(skipped["file_too_large"], 1)

    def test_retrieves_fixture_content_at_selected_commit(self):
        result = retrieve_repository_sources("https://github.com/octo/demo", "focus on API", FakeGitHubClient())
        self.assertGreater(result["retrieval_coverage"]["files_retrieved"], 0)
        self.assertTrue(all(item["commit_sha"] == "a" * 40 for item in result["selected_files"]))
        main = next(item for item in result["selected_files"] if item["path"] == "src/main.py")
        self.assertEqual(main["content"], "def main():\n    return 'fixture'\n")

    def test_total_content_limit_is_reported(self):
        with patch("backend.retrieval.MAX_TOTAL_SOURCE_BYTES", 10):
            result = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        self.assertFalse(result["retrieval_coverage"]["complete"])
        self.assertGreater(result["retrieval_coverage"]["skipped"]["total_content_limit"], 0)

    def test_file_count_limit_is_reported(self):
        entries = [{"path": f"src/module_{index}.py", "type": "blob", "mode": "100644", "size": 1} for index in range(25)]
        selected, skipped = select_files(entries)
        self.assertEqual(len(selected), 24)
        self.assertEqual(skipped["retrieval_file_limit"], 1)

    def test_unavailable_file_is_reported_without_failing_retrieval(self):
        result = retrieve_repository_sources("https://github.com/octo/demo", "", UnavailableContentClient())
        self.assertEqual(result["retrieval_coverage"]["skipped"]["file_unavailable"], 1)
        self.assertFalse(result["retrieval_coverage"]["complete"])

    def test_retrieve_endpoint_returns_selected_files(self):
        app = create_app()
        app.config.update(TESTING=True, GITHUB_CLIENT_FACTORY=FakeGitHubClient)
        response = app.test_client().post("/api/retrieve", json={"url": "https://github.com/octo/demo", "instruction": "API"})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("selected_files", payload)
        self.assertIn("retrieval_coverage", payload)


class AnalysisTests(unittest.TestCase):
    def test_context_contains_genuine_sources_commit_and_incomplete_coverage(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "Explain the API", FakeGitHubClient())
        retrieval["retrieval_coverage"].update(
            complete=False, message="Fixture retrieval reached a safety limit.", skipped={"retrieval_file_limit": 1}
        )
        request = prepare_analysis_request(retrieval)
        self.assertEqual(request.commit_sha, "a" * 40)
        self.assertIn('<file path="src/main.py"', request.user_prompt)
        self.assertIn("def main():", request.user_prompt)
        self.assertIn("Retrieval coverage is incomplete", request.user_prompt)
        self.assertIn("Explain the API", request.user_prompt)
        self.assertEqual(request.mode, "custom")
        self.assertEqual(request.repository_metadata["full_name"], "octo/demo")
        self.assertEqual(request.repository_url, "https://github.com/octo/demo")
        self.assertTrue(any(file.path == "src/main.py" and "def main" in file.content for file in request.source_files))
        self.assertFalse(request.retrieval_coverage["complete"])

    def test_url_only_uses_the_bounded_default_overview_mode(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        request = prepare_analysis_request(retrieval)
        self.assertIn("Response mode: default overview.", request.user_prompt)
        self.assertIn("72–92 words", request.user_prompt)
        self.assertIn("60–100-word range", request.user_prompt)
        self.assertIn("never exceed 120 words", request.user_prompt)
        self.assertNotIn("User instruction:", request.user_prompt)
        self.assertNotIn("Provide a general repository analysis", request.user_prompt)

    def test_specific_instruction_is_forwarded_without_default_overview(self):
        retrieval = retrieve_repository_sources(
            "https://github.com/octo/demo", "Which file defines the application entry point?", FakeGitHubClient()
        )
        request = prepare_analysis_request(retrieval)
        self.assertIn("Response mode: user-directed request.", request.user_prompt)
        self.assertIn("User instruction: Which file defines the application entry point?", request.user_prompt)
        self.assertIn("Do not add the default overview", request.user_prompt)
        self.assertNotIn("Response mode: default overview.", request.user_prompt)

    def test_comprehensive_instruction_preserves_requested_scope(self):
        instruction = "Give a comprehensive architecture report with a component-by-component breakdown."
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", instruction, FakeGitHubClient())
        request = prepare_analysis_request(retrieval)
        self.assertIn(instruction, request.user_prompt)
        self.assertIn("Respect an explicitly requested detailed or comprehensive scope.", request.user_prompt)
        self.assertNotIn("never more than 120 words", request.user_prompt)

    def test_architecture_mode_requests_a_grounded_diagram_schema(self):
        retrieval = retrieve_repository_sources(
            "https://github.com/octo/demo", "Focus on the application entry point.", FakeGitHubClient(), "architecture"
        )
        request = prepare_analysis_request(retrieval)
        self.assertEqual(retrieval["retrieval_coverage"]["mode"], "architecture")
        self.assertIn("Response mode: architecture.", request.user_prompt)
        self.assertIn("repopilot-architecture", request.user_prompt)
        self.assertIn("evidence_paths", request.user_prompt)
        self.assertIn("meaningful modules", request.user_prompt)
        self.assertIn("Focus note: Focus on the application entry point.", request.user_prompt)

    def test_unknown_mode_remains_backward_compatible(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "unexpected")
        self.assertIsNone(retrieval["retrieval_coverage"]["mode"])
        self.assertIn("Response mode: default overview.", prepare_analysis_request(retrieval).user_prompt)

    def test_provider_neutral_analysis_result_preserves_retrieval(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        result = analyze_retrieval(retrieval, FakeAnalysisProvider())
        self.assertEqual(result["analysis"]["provider"], "fake")
        self.assertEqual(result["analysis"]["commit_sha"], "a" * 40)
        self.assertIn("`src/main.py`", result["analysis"]["text"])
        self.assertEqual(result["analysis"]["source_references"], ["src/main.py"])

    def test_test_only_provider_handles_all_product_modes_through_one_contract(self):
        provider = ContractFakeProvider()
        overview = analyze_retrieval(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "overview"), provider)
        architecture = analyze_retrieval(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "architecture"), provider)
        custom = analyze_retrieval(retrieve_repository_sources("https://github.com/octo/demo", "Which file starts it?", FakeGitHubClient(), "custom"), provider)
        self.assertEqual(overview["analysis"]["mode"], "overview")
        self.assertEqual(architecture["analysis"]["architecture"]["components"][0]["paths"], ["src/main.py"])
        self.assertEqual(custom["analysis"]["mode"], "custom")
        self.assertIn("entry point", custom["analysis"]["text"])

    def test_architecture_contract_only_accepts_retrieved_paths_and_known_connections(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "architecture")
        request = prepare_analysis_request(retrieval)
        response = AnalysisResponse(
            text="""```repopilot-architecture
{"components":[{"id":"entry","label":"Entry point","kind":"cli","paths":["src/main.py"],"description":"Starts the app"},{"id":"invented","label":"Invented","kind":"service","paths":["not/real.py"]}],"connections":[{"from":"entry","to":"invented","label":"calls"}],"limitations":"Fixture-only evidence"}
```""",
            provider="fake", model="fixture-model",
        )
        result = analysis_payload(request, response)
        self.assertEqual([item["id"] for item in result["architecture"]["components"]], ["entry"])
        self.assertEqual(result["architecture"]["connections"], [])
        self.assertEqual(result["architecture"]["components"][0]["paths"], ["src/main.py"])

    def test_architecture_contract_keeps_real_modules_and_evidence_backed_edges(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "architecture")
        request = prepare_analysis_request(retrieval)
        response = AnalysisResponse(
            text="""```repopilot-architecture
{"modules":[{"id":"application","label":"Application","paths":["src/main.py"]}],"components":[{"id":"entry","label":"main.py","kind":"cli","module":"application","paths":["src/main.py"],"description":"Entry point"},{"id":"tests","label":"test_main.py","kind":"config","paths":["tests/test_main.py"]}],"connections":[{"from":"entry","to":"tests","label":"uses","evidence_paths":["src/main.py"]},{"from":"tests","to":"entry","label":"uses","evidence_paths":["README.md"]}]}
```""", provider="fake", model="fixture",
        )
        architecture = analysis_payload(request, response)["architecture"]
        self.assertEqual(architecture["modules"][0]["id"], "application")
        self.assertEqual(architecture["components"][0]["module"], "application")
        self.assertEqual(architecture["connections"], [{"label": "uses", "evidence_paths": ["src/main.py"], "from": "entry", "to": "tests"}])

    def test_provider_supplied_architecture_data_uses_the_same_contract(self):
        request = prepare_analysis_request(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "architecture"))
        response = AnalysisResponse(
            text="Architecture generated by a fake provider.", provider="fake", model="fixture-model",
            architecture=ArchitectureData(
                components=(ArchitectureComponent("entry", "Entry point", "cli", ("src/main.py",), "Starts the app"),),
                connections=(ArchitectureConnection("entry", "entry", "invalid self relation"),),
            ),
        )
        # Provider-native data is typed and still receives the same evidence checks.
        result = analysis_payload(request, response)
        self.assertEqual(result["architecture"]["components"][0]["id"], "entry")
        self.assertEqual(result["architecture"]["connections"], [])

    def test_invalid_architecture_text_is_ignored_without_affecting_normal_analysis(self):
        request = prepare_analysis_request(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient(), "architecture"))
        self.assertIsNone(parse_architecture_data("```repopilot-architecture\nnot json\n```", request.source_files))

    def test_response_references_are_limited_to_retrieved_files(self):
        request = prepare_analysis_request(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient()))
        result = analysis_payload(request, AnalysisResponse(
            text="Answer.", provider="fake", model="fixture", source_references=("src/main.py", "not/retrieved.py"),
        ))
        self.assertEqual(result["source_references"], ["src/main.py"])

    def test_anthropic_provider_uses_messages_api_with_prepared_prompt(self):
        request = prepare_analysis_request(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient()))
        with patch("backend.providers.anthropic_provider.anthropic.Anthropic") as client_class:
            client_class.return_value.messages.create.return_value = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Grounded response citing `src/main.py`.")]
            )
            response = AnthropicProvider(api_key="test-key", model="test-model").analyze(request)
        self.assertEqual(response.provider, "anthropic")
        self.assertEqual(response.model, "test-model")
        call = client_class.return_value.messages.create.call_args.kwargs
        self.assertEqual(call["model"], "test-model")
        self.assertIn("src/main.py", call["messages"][0]["content"])
        self.assertEqual(call["system"], load_analysis_instructions())

    def test_anthropic_provider_forwards_native_text_stream_in_order(self):
        request = prepare_analysis_request(retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient()))

        class NativeStream:
            text_stream = iter(["First ", "second", " third."])

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("backend.providers.anthropic_provider.anthropic.Anthropic") as client_class:
            client_class.return_value.messages.stream.return_value = NativeStream()
            chunks = list(AnthropicProvider(api_key="test-key", model="test-model").stream_analyze(request))
        self.assertEqual(chunks, ["First ", "second", " third."])
        call = client_class.return_value.messages.stream.call_args.kwargs
        self.assertEqual(call["model"], "test-model")
        self.assertIn("src/main.py", call["messages"][0]["content"])

    def test_central_instruction_file_contains_grounding_rules(self):
        instructions = load_analysis_instructions()
        self.assertIn("untrusted reference material", instructions)
        self.assertIn("Do not invent", instructions)
        self.assertIn("file paths", instructions)
        self.assertIn("## Project summary", instructions)
        self.assertIn("Do not create unrequested sections", instructions)

    def test_provider_selection_rejects_unavailable_provider(self):
        with patch.dict(os.environ, {"ANALYSIS_PROVIDER": "future-provider"}, clear=False):
            with self.assertRaises(DiscoveryError) as raised:
                get_analysis_provider()
        self.assertEqual(raised.exception.code, "analysis_provider_unavailable")

    def test_analysis_endpoint_uses_configured_provider_factory(self):
        app = create_app()
        app.config.update(TESTING=True, GITHUB_CLIENT_FACTORY=FakeGitHubClient, ANALYSIS_PROVIDER_FACTORY=FakeAnalysisProvider)
        response = app.test_client().post("/api/analyze", json={"url": "https://github.com/octo/demo"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analysis"]["provider"], "fake")

    def test_analysis_does_not_overwrite_verified_github_metadata(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        result = analyze_retrieval(retrieval, FakeAnalysisProvider())
        self.assertEqual(result["repository"]["stars"], 7)
        self.assertEqual(result["repository"]["languages"], {"Python": 120, "Shell": 20})
        self.assertEqual(result["analysis"]["provider"], "fake")

    def test_follow_up_context_includes_history_question_and_original_commit(self):
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        request = prepare_follow_up_request(
            retrieval,
            "Which file starts the application?",
            [{"role": "user", "text": "Give an overview."}, {"role": "assistant", "text": "See `src/main.py`."}],
        )
        self.assertEqual(request.commit_sha, "a" * 40)
        self.assertIn("Which file starts the application?", request.user_prompt)
        self.assertIn("User: Give an overview.", request.user_prompt)
        self.assertIn("RepoPilot: See `src/main.py`.", request.user_prompt)
        self.assertIn('<file path="src/main.py"', request.user_prompt)
        self.assertIn("Response mode: follow-up.", request.user_prompt)
        self.assertIn("Do not repeat the initial repository overview", request.user_prompt)
        self.assertNotIn("Response mode: default overview.", request.user_prompt)
        self.assertEqual(request.instruction, "Which file starts the application?")
        self.assertEqual([turn.role for turn in request.conversation_history], ["user", "assistant"])


class FollowUpRetrievalTests(unittest.TestCase):
    def test_retrieves_question_relevant_files_at_original_commit(self):
        initial = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        initial["selected_files"] = [next(item for item in initial["selected_files"] if item["path"] == "src/main.py")]
        FollowUpGitHubClient.requested_refs = []
        steps = stream_retrieve_follow_up_sources(initial, "Explain authentication", FollowUpGitHubClient())
        updates = []
        while True:
            try:
                updates.append(next(steps))
            except StopIteration as complete:
                follow_up = complete.value
                break
        self.assertIn("src/auth.py", [item["path"] for item in follow_up["selected_files"]])
        self.assertEqual(follow_up["retrieval_coverage"]["commit_sha"], "a" * 40)
        self.assertTrue(all(ref == "a" * 40 for ref in FollowUpGitHubClient.requested_refs))
        self.assertTrue(any(update["event"] == "retrieval" for update in updates))

    def test_temporary_context_reuses_and_limits_sources(self):
        cache = ConversationCache()
        retrieval = retrieve_repository_sources("https://github.com/octo/demo", "", FakeGitHubClient())
        identifier = cache.store(retrieval, "browser-local-conversation")
        restored = cache.retrieval(identifier)
        self.assertEqual(restored["retrieval_coverage"]["commit_sha"], "a" * 40)
        cache.merge_sources(identifier, retrieval)
        self.assertEqual(cache.retrieval(identifier)["repository"]["full_name"], "octo/demo")


class StreamingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, GITHUB_CLIENT_FACTORY=FakeGitHubClient, ANALYSIS_PROVIDER_FACTORY=FakeStreamingProvider)
        self.client = self.app.test_client()

    def test_sse_format_is_parseable(self):
        self.assertEqual(sse("token", {"text": "hello"}), 'event: token\ndata: {"text": "hello"}\n\n')

    def test_stream_forwards_ordered_chunks_and_completes(self):
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.mimetype, "text/event-stream")
        self.assertLess(body.index('data: {"text": "## Overview\\n"}'), body.index('data: {"text": "Verified fixture analysis'))
        self.assertIn("event: repository", body)
        self.assertIn("event: retrieval", body)
        self.assertIn("event: context", body)
        self.assertIn("event: done", body)
        done_payload = json.loads(re.search(r"event: done\ndata: (.+)", body).group(1))
        self.assertEqual(done_payload["source_files"][0]["commit_sha"], "a" * 40)
        self.assertIn("content", done_payload["source_files"][0])
        self.assertNotIn("event: error", body)

    def test_stream_uses_the_same_default_overview_request_preparation(self):
        CapturingStreamingProvider.requests = []
        self.app.config["ANALYSIS_PROVIDER_FACTORY"] = CapturingStreamingProvider
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo"})
        self.assertIn("event: done", response.get_data(as_text=True))
        request = CapturingStreamingProvider.requests[-1]
        self.assertIn("Response mode: default overview.", request.user_prompt)
        self.assertIn("never exceed 120 words", request.user_prompt)

    def test_stream_forwards_architecture_mode_to_request_preparation(self):
        CapturingStreamingProvider.requests = []
        self.app.config["ANALYSIS_PROVIDER_FACTORY"] = CapturingStreamingProvider
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo", "mode": "architecture"})
        self.assertIn("event: done", response.get_data(as_text=True))
        request = CapturingStreamingProvider.requests[-1]
        self.assertIn("Response mode: architecture.", request.user_prompt)

    def test_interrupted_stream_emits_error_without_done(self):
        self.app.config["ANALYSIS_PROVIDER_FACTORY"] = FailingStreamingProvider
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo"})
        body = response.get_data(as_text=True)
        self.assertIn("event: token", body)
        self.assertIn("event: error", body)
        self.assertNotIn("event: done", body)

    def test_github_failure_never_emits_success(self):
        self.app.config["GITHUB_CLIENT_FACTORY"] = UnavailableGitHubClient
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/missing"})
        body = response.get_data(as_text=True)
        self.assertIn("event: error", body)
        self.assertNotIn("event: token", body)
        self.assertNotIn("event: done", body)

    def test_partial_source_coverage_is_returned_in_done_event(self):
        self.app.config["GITHUB_CLIENT_FACTORY"] = UnavailableContentClient
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo"})
        body = response.get_data(as_text=True)
        done_payload = json.loads(re.search(r"event: done\ndata: (.+)", body).group(1))
        self.assertFalse(done_payload["retrieval_coverage"]["complete"])
        self.assertEqual(done_payload["retrieval_coverage"]["skipped"]["file_unavailable"], 1)


class FollowUpStreamingTests(unittest.TestCase):
    def setUp(self):
        CapturingStreamingProvider.requests = []
        self.app = create_app()
        self.app.config.update(TESTING=True, GITHUB_CLIENT_FACTORY=FakeGitHubClient, ANALYSIS_PROVIDER_FACTORY=CapturingStreamingProvider)
        self.client = self.app.test_client()

    def _initial_conversation_id(self):
        response = self.client.post("/api/analyze/stream", json={"url": "https://github.com/octo/demo", "conversation_id": "browser-local-fixture"})
        payload = json.loads(re.search(r"event: done\ndata: (.+)", response.get_data(as_text=True)).group(1))
        return payload["conversation_id"]

    def test_follow_up_streams_using_saved_context(self):
        conversation_id = self._initial_conversation_id()
        response = self.client.post("/api/conversations/follow-up/stream", json={
            "conversation_id": conversation_id,
            "question": "Which file is the entry point?",
            "history": [{"role": "user", "text": "Give an overview."}],
        })
        body = response.get_data(as_text=True)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        self.assertNotIn("event: error", body)
        request = CapturingStreamingProvider.requests[-1]
        self.assertEqual(request.commit_sha, "a" * 40)
        self.assertIn("Which file is the entry point?", request.user_prompt)

    def test_missing_or_expired_context_never_emits_done(self):
        response = self.client.post("/api/conversations/follow-up/stream", json={"conversation_id": "missing-context-identifier", "question": "What does this do?"})
        body = response.get_data(as_text=True)
        self.assertIn("event: error", body)
        self.assertIn("conversation_context_expired", body)
        self.assertNotIn("event: done", body)
