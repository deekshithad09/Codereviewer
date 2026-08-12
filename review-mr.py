import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aviator Merge Request Review Runner")
    parser.add_argument("--projectId", type=int, required=False, help="GitLab project ID")
    parser.add_argument("--mergeRequestIid", type=int, required=False, help="Merge request IID")
    parser.add_argument("--gitlabToken", type=str, required=False, help="GitLab API token")
    parser.add_argument("--gitlabUrl", type=str, required=False, help="GitLab URL")
    parser.add_argument("--llmApiKey", type=str, required=False, help="LLM API key")
    parser.add_argument("--llmApiUrl", type=str, required=False, help="LLM API URL")
    parser.add_argument("--llmModel", type=str, required=False, help="LLM model")
    parser.add_argument(
        "--enableCodeReview",
        type=lambda v: str(v).lower() in {"1", "true", "yes", "on"},
        required=False,
        help="Enable or disable code review execution",
    )
    parser.add_argument(
        "--guidelinesContent",
        type=str,
        required=False,
        help="Full content of a guidelines file to use for code review (overrides mapping-based detection)",
    )
    return parser.parse_args()


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_added_lines(diff: Optional[str]) -> List[Dict[str, str]]:
    if not diff:
        return []

    added = []
    for line in diff.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added.append({"content": line[1:], "line": line})
    return added


def extract_removed_lines(diff: Optional[str]) -> List[Dict[str, str]]:
    if not diff:
        return []

    removed = []
    for line in diff.split("\n"):
        if line.startswith("-") and not line.startswith("---"):
            removed.append({"content": line[1:], "line": line})
    return removed


def extract_modified_lines(diff: Optional[str]) -> List[Dict[str, Any]]:
    if not diff:
        return []

    lines = diff.split("\n")
    modifications: List[Dict[str, Any]] = []
    current_chunk: Optional[Dict[str, Any]] = None
    current_new_line_number = 0
    current_old_line_number = 0

    for line in lines:
        if line.startswith("@@"):
            if current_chunk:
                modifications.append(current_chunk)

            match = re.search(r"@@ -(\d+),?\d* \+(\d+),?\d* @@", line)
            current_old_line_number = int(match.group(1)) if match else 0
            current_new_line_number = int(match.group(2)) if match else 0
            current_chunk = {
                "header": line,
                "oldLineStart": current_old_line_number,
                "newLineStart": current_new_line_number,
                "changes": [],
            }
        elif current_chunk is not None and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            if line.startswith("+"):
                change_type = "added"
            elif line.startswith("-"):
                change_type = "removed"
            else:
                change_type = "context"

            change: Dict[str, Any] = {
                "type": change_type,
                "content": line[1:],
                "originalLine": line,
            }

            if change_type == "added":
                change["newLineNumber"] = current_new_line_number
                current_new_line_number += 1
            elif change_type == "removed":
                change["oldLineNumber"] = current_old_line_number
                current_old_line_number += 1
            else:
                change["newLineNumber"] = current_new_line_number
                change["oldLineNumber"] = current_old_line_number
                current_new_line_number += 1
                current_old_line_number += 1

            current_chunk["changes"].append(change)

    if current_chunk:
        modifications.append(current_chunk)

    return modifications


def load_guideline_mappings() -> List[Dict[str, Any]]:
    mappings_json = os.getenv("GUIDELINE_MAPPINGS")

    if not mappings_json:
        print("INFO: GUIDELINE_MAPPINGS not found in environment. Loading default mappings from guideline.mappings.json")
        try:
            default_path = Path(__file__).resolve().parent / "guideline.mappings.json"
            mappings_json = default_path.read_text(encoding="utf-8")
        except OSError as err:
            print(f"WARN: Could not load default mappings file: {err}")
            print("WARN: Using empty mappings.")
            return []

    try:
        mappings = json.loads(mappings_json)
        normalized = []
        for mapping in mappings:
            files = []
            if isinstance(mapping.get("files"), list):
                files = mapping["files"]
            elif isinstance(mapping.get("file"), str):
                files = [mapping["file"]]

            normalized.append(
                {
                    **mapping,
                    "files": files,
                    "pattern": re.compile(mapping["pattern"]),
                }
            )
        return normalized
    except Exception as err:  # noqa: BLE001
        print(f"ERROR: Error parsing GUIDELINE_MAPPINGS from environment: {err}")
        print("Please ensure GUIDELINE_MAPPINGS is valid JSON format.")
        return []


GUIDELINE_MAPPINGS = load_guideline_mappings()


def detect_required_guidelines(file_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    required: List[Dict[str, Any]] = []

    for file_entry in file_contents:
        file_path = file_entry["path"]
        for mapping in GUIDELINE_MAPPINGS:
            if mapping["pattern"].search(file_path) and mapping not in required:
                required.append(mapping)

    return required


def read_guideline_files(guidelines: List[Dict[str, Any]]) -> str:
    if not guidelines:
        return ""

    content = "\n\n## RELEVANT GUIDELINES FOR THIS REVIEW\n\n"
    read_files = set()

    for guideline in guidelines:
        for file_path in guideline.get("files", []):
            if file_path in read_files:
                print(f"SKIP: Duplicate guideline: {file_path}")
                continue

            read_files.add(file_path)
            try:
                absolute_path = Path(file_path).resolve()
                print(f"READ: Reading guideline: {file_path}")
                guideline_text = absolute_path.read_text(encoding="utf-8")
                content += f"### {guideline.get('description', '')}\n\n"
                content += f"Source: {file_path}\n\n"
                content += guideline_text + "\n\n---\n\n"
            except OSError as err:
                print(f"WARN: Could not read guideline file: {file_path} - {err}")

    return content


def read_prompt_file() -> str:
    prompt_file_path = os.getenv("LLM_PROMPT_FILE")
    if not prompt_file_path:
        raise RuntimeError("LLM_PROMPT_FILE environment variable is not set")

    return Path(prompt_file_path).resolve().read_text(encoding="utf-8")


def get_merge_request_files(
    project_id: str,
    merge_request_iid: str,
    gitlab_url: str,
    gitlab_token: str,
) -> List[Dict[str, Any]]:
    changes_url = f"{gitlab_url}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/changes"
    headers = {"Authorization": f"Bearer {gitlab_token}"}

    try:
        print("\nGitLab API Request - Fetching MR Changes")
        print(f"URL: {changes_url}")
        auth_preview = f"Bearer {gitlab_token[:10]}..." if gitlab_token else "NOT SET"
        print(f"Authorization: {auth_preview}")

        changes_response = requests.get(changes_url, headers=headers, timeout=30)
        changes_response.raise_for_status()
        changes_data = changes_response.json()

        print(f"OK: MR Changes Response Status: {changes_response.status_code}")
        print(f"Source Branch: {changes_data.get('source_branch')}")
        print(f"Target Branch: {changes_data.get('target_branch')}")
        print(f"Number of changed files: {len(changes_data.get('changes') or [])}")

        file_contents: List[Dict[str, Any]] = []
        source_branch = changes_data.get("source_branch")

        for change in changes_data.get("changes") or []:
            if change.get("deleted_file"):
                print(f"SKIP: Skipping deleted file: {change.get('old_path')}")
                continue

            new_path = change.get("new_path")
            encoded_path = quote(new_path or "", safe="")
            file_url = f"{gitlab_url}/api/v4/projects/{project_id}/repository/files/{encoded_path}"

            try:
                print(f"\nFILE: Fetching file: {new_path}")
                print(f"File URL: {file_url}")
                print(f"Ref: {source_branch}")

                file_response = requests.get(
                    file_url,
                    params={"ref": source_branch},
                    headers=headers,
                    timeout=30,
                )
                file_response.raise_for_status()
                file_data = file_response.json()

                decoded_content = base64.b64decode(file_data.get("content", "")).decode("utf-8")
                print(f"OK: File fetched successfully: {new_path}")

                diff_text = change.get("diff", "")
                file_contents.append(
                    {
                        "path": new_path,
                        "content": decoded_content,
                        "diff": diff_text,
                        "new_file": change.get("new_file", False),
                        "renamed_file": change.get("renamed_file", False),
                        "old_path": change.get("old_path"),
                        "modifiedLines": extract_modified_lines(diff_text),
                        "addedLines": extract_added_lines(diff_text),
                        "removedLines": extract_removed_lines(diff_text),
                    }
                )
            except requests.RequestException as file_err:
                print(f"ERROR: Error fetching file {new_path}: {file_err}")
                if file_err.response is not None:
                    print(f"Response status: {file_err.response.status_code}")
                    try:
                        print(f"Response data: {file_err.response.json()}")
                    except ValueError:
                        print(f"Response data: {file_err.response.text}")

        return file_contents
    except requests.RequestException as err:
        print(f"\nERROR: Error fetching merge request files: {err}")
        if err.response is not None:
            print(f"Response status: {err.response.status_code}")
            print(f"Response statusText: {err.response.reason}")
            try:
                print("Response data:", json.dumps(err.response.json(), indent=2))
            except ValueError:
                print(f"Response data: {err.response.text}")
        raise


def format_changes_for_llm(file_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formatted_changes = []

    for file in file_contents:
        file_type = (file.get("path") or "").split(".")[-1] if "." in (file.get("path") or "") else ""

        file_info: Dict[str, Any] = {
            "file_path": file.get("path"),
            "file_type": file_type,
            "change_type": "new_file"
            if file.get("new_file")
            else "renamed"
            if file.get("renamed_file")
            else "modified",
            "old_path": file.get("old_path"),
            "summary": {
                "added_lines": len(file.get("addedLines", [])),
                "removed_lines": len(file.get("removedLines", [])),
                "total_changes": len(file.get("modifiedLines", [])),
            },
        }

        if file.get("new_file") and file.get("content"):
            file_info["new_content_preview"] = "\n".join(file["content"].split("\n")[:20])

        changes = []
        for chunk in file.get("modifiedLines", []):
            chunk_modifications = []
            for change in chunk.get("changes", []):
                if change.get("type") not in {"added", "removed"}:
                    continue

                line_number = (
                    change.get("newLineNumber")
                    if change.get("type") == "added"
                    else change.get("oldLineNumber")
                )
                chunk_modifications.append(
                    {
                        "type": change.get("type"),
                        "content": change.get("content"),
                        "line": change.get("originalLine"),
                        "lineNumber": line_number,
                    }
                )

            changes.append({"location": chunk.get("header"), "modifications": chunk_modifications})

        file_info["changes"] = changes
        formatted_changes.append(file_info)

    return formatted_changes


def format_changes_for_llm_details(formatted_changes: List[Dict[str, Any]]) -> str:
    prompt = ""

    for index, file in enumerate(formatted_changes, start=1):
        prompt += f"## File {index}: {file.get('file_path')}\n"
        prompt += (
            f"**Type**: {file.get('change_type')} | **Language**: {file.get('file_type')} | "
            f"**Changes**: +{file.get('summary', {}).get('added_lines', 0)} "
            f"-{file.get('summary', {}).get('removed_lines', 0)} lines\n\n"
        )

        if file.get("old_path") and file.get("old_path") != file.get("file_path"):
            prompt += f"**Renamed from**: {file.get('old_path')}\n\n"

        for chunk_index, chunk in enumerate(file.get("changes", []), start=1):
            if chunk.get("modifications"):
                prompt += f"### Change Block {chunk_index} - {chunk.get('location')}\n\n"
                prompt += "**IMPORTANT: Use the line numbers shown in parentheses for your review feedback**\n\n"
                prompt += "```diff\n"

                for mod in chunk.get("modifications", []):
                    line_number = mod.get("lineNumber")
                    line_info = f" (Line {line_number})" if line_number is not None else ""
                    prompt += f"{mod.get('line', '')}{line_info}\n"

                prompt += "```\n\n"

        if file.get("new_content_preview"):
            prompt += "### New File Content Preview:\n"
            prompt += "**IMPORTANT: Line numbers are shown at the start of each line**\n\n"
            prompt += f"```{file.get('file_type', '')}\n"
            for idx, line in enumerate(file["new_content_preview"].split("\n"), start=1):
                prompt += f"{idx}: {line}\n"
            prompt += "```\n\n"

        prompt += "---\n\n"

    return prompt


def review_changes_with_llm(
    formatted_changes: List[Dict[str, Any]],
    file_contents: List[Dict[str, Any]],
    llm_api_url: str,
    llm_api_key: str,
    llm_model: str,
    inline_guidelines_content: Optional[str],
) -> Dict[str, Any]:
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {llm_api_key}",
    }

    prompt = read_prompt_file()

    print("\nPreparing guidelines for code review...")
    if inline_guidelines_content:
        print("Using guidelines provided via --guidelinesContent / GUIDELINES_CONTENT")
        resolved_guidelines_content = (
            "\n\n## GUIDELINES FOR THIS REVIEW\n\n"
            f"{inline_guidelines_content}\n\n---\n\n"
        )
    else:
        required_guidelines = detect_required_guidelines(file_contents)
        desc = ", ".join(g.get("description", "") for g in required_guidelines) or "None"
        print(f"Required guidelines: {desc}")
        resolved_guidelines_content = read_guideline_files(required_guidelines)

    prompt_content = f"{prompt}{resolved_guidelines_content}\n\n{format_changes_for_llm_details(formatted_changes)}"

    payload = {
        "model": llm_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert code reviewer. Analyze the provided code changes and provide "
                    "constructive feedback focusing on code quality, security, performance, and best "
                    "practices. Be concise but thorough."
                ),
            },
            {
                "role": "user",
                "content": prompt_content,
            },
        ],
    }

    try:
        print("\n=== LLM API Request (Code Review) ===")
        print(f"URL: {llm_api_url}")
        print(f"Model: {llm_model}")
        auth_preview = f"Bearer {llm_api_key[:10]}..." if llm_api_key else "NOT SET"
        print(f"Authorization: {auth_preview}")
        print(
            "Payload structure:",
            {
                "model": payload["model"],
                "messages": [
                    {"role": m["role"], "contentLength": len(m["content"])} for m in payload["messages"]
                ],
                "messageCount": len(payload["messages"]),
            },
        )
        print(
            "Headers:",
            {
                "content-type": headers["content-type"],
                "authorization": "Bearer ***" if headers["authorization"] else "NOT SET",
            },
        )

        response = requests.post(llm_api_url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        response_data = response.json()

        print(f"OK: LLM API Response Status: {response.status_code}")
        has_choices = bool(response_data.get("choices"))
        first_choice = response_data.get("choices", [{}])[0] if has_choices else {}
        has_message = bool(first_choice.get("message"))
        has_content = bool(first_choice.get("message", {}).get("content"))
        print(
            "Response data structure:",
            {
                "hasChoices": has_choices,
                "choicesLength": len(response_data.get("choices", [])),
                "hasMessage": has_message,
                "hasContent": has_content,
            },
        )

        review_content = first_choice.get("message", {}).get("content") or "No review content received"
        return {
            "model": llm_model,
            "review": review_content,
            "usage": response_data.get("usage"),
            "timestamp": iso_utc_now(),
            "response_format": "json",
        }
    except requests.RequestException as err:
        print("\nERROR: LLM API Error (Code Review):")
        print(f"Error message: {err}")
        if err.response is not None:
            print(f"Response status: {err.response.status_code}")
            print(f"Response statusText: {err.response.reason}")
            try:
                print("Response data:", json.dumps(err.response.json(), indent=2))
            except ValueError:
                print(f"Response data: {err.response.text}")
            print(f"Response headers: {dict(err.response.headers)}")
        else:
            print("No response received")

        return {
            "model": llm_model,
            "review": f"Unable to complete code review due to API issues. Error: {err}",
            "usage": None,
            "timestamp": iso_utc_now(),
            "error": str(err),
            "response_format": "error",
        }


def format_review_comment(
    review_result: Dict[str, Any],
    file_contents: List[Dict[str, Any]],
    formatted_changes: List[Dict[str, Any]],
) -> str:
    timestamp = iso_utc_now()
    total_files = len(file_contents)
    total_added = sum(len(file.get("addedLines", [])) for file in file_contents)
    total_removed = sum(len(file.get("removedLines", [])) for file in file_contents)

    comment = "# Aviator Code Review Feedback\n\n"
    comment += "## Summary\n"
    comment += f"- **Total Files Reviewed**: {total_files}\n"
    comment += f"- **Total Changes**: +{total_added} -{total_removed} lines\n"
    comment += "- **Service**: Aviator Model Sandbox\n"
    comment += f"- **Model**: {review_result.get('model')}\n"
    comment += f"- **Review Completed At**: {timestamp}\n\n"

    if review_result.get("error"):
        comment += "WARNING: **Review completed with errors:**\n\n"
        comment += f"```\n{review_result.get('error')}\n```\n\n"

    review_content = (review_result.get("review") or "").strip()
    has_violations = "❌" in review_content

    if not has_violations:
        if "no wrong implementation" in review_content.lower():
            review_content = "No wrong implementations found."
        else:
            review_content = "No wrong implementations found."

    comment += review_content

    comment += "\n\n## Files Summary\n"
    for file in formatted_changes:
        summary = file.get("summary", {})
        comment += (
            f"- **{file.get('file_path')}** ({file.get('change_type')}): "
            f"+{summary.get('added_lines', 0)} -{summary.get('removed_lines', 0)} lines\n"
        )

    comment += (
        "\n\n---\n"
        "**This review was generated automatically by Aviator. Please use your judgment when applying suggestions.**"
    )

    return comment


def post_review_comment(
    project_id: str,
    merge_request_iid: str,
    comment: str,
    gitlab_url: str,
    gitlab_token: str,
) -> Dict[str, Any]:
    try:
        print(f"POST: Posting review comment to MR {merge_request_iid}...")
        response = requests.post(
            f"{gitlab_url}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/notes",
            json={"body": comment},
            headers={
                "Authorization": f"Bearer {gitlab_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        print("OK: Review comment posted successfully")
        print(f"Comment ID: {data.get('id')}")
        return data
    except requests.RequestException as err:
        print(f"ERROR: Error posting review comment: {err}")
        if err.response is not None:
            try:
                print(f"Response data: {err.response.json()}")
            except ValueError:
                print(f"Response data: {err.response.text}")
        raise


def main() -> None:
    args = parse_args()

    project_id = args.projectId or os.getenv("CI_PROJECT_ID")
    merge_request_iid = args.mergeRequestIid or os.getenv("CI_MERGE_REQUEST_IID")
    gitlab_token = args.gitlabToken or os.getenv("GITLAB_TOKEN")
    gitlab_url = args.gitlabUrl or os.getenv("GITLAB_URL")
    llm_api_key = args.llmApiKey or os.getenv("LLM_API_KEY")
    llm_api_url = args.llmApiUrl or os.getenv("LLM_API_URL")
    llm_model = args.llmModel or os.getenv("LLM_MODEL")
    enable_code_review = (
        args.enableCodeReview
        if args.enableCodeReview is not None
        else os.getenv("ENABLE_CODE_REVIEW", "false").lower() == "true"
    )
    guidelines_content = args.guidelinesContent or os.getenv("GUIDELINES_CONTENT") or None

    missing = [
        not project_id and "--projectId (or CI_PROJECT_ID)",
        not merge_request_iid and "--mergeRequestIid (or CI_MERGE_REQUEST_IID)",
        not gitlab_token and "--gitlabToken (or GITLAB_TOKEN)",
        not gitlab_url and "--gitlabUrl (or GITLAB_URL)",
        not llm_api_key and "--llmApiKey (or LLM_API_KEY)",
        not llm_api_url and "--llmApiUrl (or LLM_API_URL)",
        not llm_model and "--llmModel (or LLM_MODEL)",
    ]
    missing = [m for m in missing if m]

    if missing:
        print("Missing required arguments:\n  " + "\n  ".join(missing), file=sys.stderr)
        sys.exit(1)

    if not enable_code_review:
        print("Code review execution is disabled.")
        sys.exit(0)

    try:
        print("\n=== Starting Code Review Process ===")
        print(f"Project ID: {project_id}")
        print(f"Merge Request IID: {merge_request_iid}")
        print(f"GitLab URL: {gitlab_url}")
        print(f"LLM API URL: {llm_api_url}")
        print(f"LLM Model: {llm_model}")

        print("\nFETCH: Fetching merge request files...")
        file_contents = get_merge_request_files(str(project_id), str(merge_request_iid), str(gitlab_url), str(gitlab_token))
        print(f"OK: Fetched {len(file_contents)} file(s)")

        print("\nFORMAT: Formatting changes for LLM...")
        formatted_changes = format_changes_for_llm(file_contents)
        print(f"OK: Formatted {len(formatted_changes)} file(s)")

        required_guidelines = detect_required_guidelines(file_contents)
        guideline_names = ", ".join(g.get("description", "") for g in required_guidelines) or "None"
        print(f"\nRequired guidelines: {guideline_names}")

        if not required_guidelines:
            print("\nSKIP: Skipping review: No applicable guideline patterns found for any files in this MR.")
            print("INFO: Only files matching GUIDELINE_MAPPINGS patterns will be reviewed.")
            return

        review_result = review_changes_with_llm(
            formatted_changes,
            file_contents,
            str(llm_api_url),
            str(llm_api_key),
            str(llm_model),
            guidelines_content,
        )

        print("\nCOMMENT: Generating review comment...")
        review_comment = format_review_comment(review_result, file_contents, formatted_changes)

        print("\n=== Review Complete ===")
        print(review_comment)

        post_review_comment(
            str(project_id),
            str(merge_request_iid),
            review_comment,
            str(gitlab_url),
            str(gitlab_token),
        )
    except Exception as err:  # noqa: BLE001
        print(f"\nERROR: Error in code review process: {err}")
        raise


if __name__ == "__main__":
    main()
