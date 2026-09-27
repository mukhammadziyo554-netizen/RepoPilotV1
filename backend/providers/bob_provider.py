import json
import os
import subprocess
import sys
from typing import Generator

from ..errors import DiscoveryError
from .base import AnalysisRequest, AnalysisResponse


class BobProvider:
    """AI provider using IBM Bob Shell's non-interactive CLI interface."""

    name = "bob"

    def __init__(self, api_key: str | None = None, team_id: str | None = None):
        key = (api_key if api_key is not None else os.getenv("BOB_API_KEY", "")).strip()
        if not key:
            raise DiscoveryError("analysis_not_configured", "IBM Bob is not configured. Add BOB_API_KEY and restart RepoPilot.", 503)
        self.api_key = key
        self.team_id = (team_id or os.getenv("BOB_TEAM_ID", "")).strip()
        self.model = "bob-2.0.5"
        self._verify_bob_available()

    def _verify_bob_available(self) -> None:
        """Verify that bob CLI is available in the environment."""
        try:
            result = subprocess.run(["bob", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise DiscoveryError("analysis_not_configured", "IBM Bob CLI is not properly configured. Verify bob is installed.", 503)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise DiscoveryError("analysis_not_configured", "IBM Bob CLI is not available. Verify bob is installed and in PATH.", 503) from error

    def _build_bob_command(self, prompt: str, format_type: str = "json") -> list[str]:
        """Build the bob run command with appropriate arguments."""
        cmd = ["bob", "run", "-f", format_type, "--accept-license"]

        # Add team ID if configured
        if self.team_id:
            cmd.extend(["--team-id", self.team_id])

        # Add the prompt
        cmd.append(prompt)

        return cmd

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        """Invoke Bob for non-streaming analysis."""
        try:
            cmd = self._build_bob_command(request.user_prompt, format_type="json")
            env = os.environ.copy()
            env["BOB_API_KEY"] = self.api_key

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Bob analysis failed"
                if "authentication" in error_msg.lower() or "unauthorized" in error_msg.lower():
                    raise DiscoveryError("analysis_authentication_failed", "IBM Bob rejected the configured API key.", 502)
                raise DiscoveryError("analysis_failed", f"IBM Bob could not complete this analysis. {error_msg[:100]}", 502)

            # Parse the JSON output
            try:
                response_data = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise DiscoveryError("analysis_failed", "IBM Bob returned invalid JSON.", 502) from error

            # Extract the last message (actual response)
            text = response_data.get("last_message", "").strip()
            if not text:
                raise DiscoveryError("analysis_failed", "IBM Bob returned an empty analysis. Please retry.", 502)

            return AnalysisResponse(text=text, provider=self.name, model=self.model)

        except DiscoveryError:
            raise
        except subprocess.TimeoutExpired:
            raise DiscoveryError("analysis_unavailable", "IBM Bob analysis timed out. Please try again.", 502)
        except Exception as error:
            raise DiscoveryError("analysis_failed", f"IBM Bob encountered an error: {str(error)[:100]}", 502) from error

    def stream_analyze(self, request: AnalysisRequest) -> Generator[str, None, None]:
        """Stream analysis from Bob using stream-json format."""
        try:
            cmd = self._build_bob_command(request.user_prompt, format_type="stream-json")
            env = os.environ.copy()
            env["BOB_API_KEY"] = self.api_key

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            try:
                # Process stream-json output line by line
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # Skip invalid JSON lines

                    # Extract assistant message chunks
                    if data.get("type") == "message" and data.get("role") == "assistant":
                        content = data.get("content", "")
                        if content:
                            yield content

                # Wait for process to complete and check for errors
                process.wait(timeout=120)
                if process.returncode != 0:
                    stderr = process.stderr.read() if process.stderr else ""
                    raise DiscoveryError("analysis_failed", f"IBM Bob encountered an error: {stderr[:100]}", 502)

            except subprocess.TimeoutExpired:
                process.kill()
                raise DiscoveryError("analysis_unavailable", "IBM Bob analysis timed out. Please try again.", 502)

        except DiscoveryError:
            raise
        except Exception as error:
            raise DiscoveryError("analysis_failed", f"IBM Bob streaming failed: {str(error)[:100]}", 502) from error
