import json
import logging
import os
import boto3
import requests
import base64

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client(service_name='bedrock-runtime')

GITHUB_HEADERS = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.environ.get("GITHUB_PAT")}',
    'User-Agent': 'lambda_function'
}

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    try:
        repo_name = event['repo_name']
        branch_name = event['branch_name']
        logs_url = event['logs_url']

        repo_files_content = fetch_files_from_github(repo_name, branch_name)

        error_message = fetch_github_actions_details(logs_url)

        if repo_files_content == '' or error_message == '':
            raise KeyError("Neither 'repo_files_content' nor 'error_message' were found.")

        steps_to_remediate = get_steps_to_remediate(repo_files_content, error_message)

        fixed_code = remediate_code(repo_files_content, steps_to_remediate)

        create_new_branch(fixed_code, repo_name)

        final_response = {'response': 'success'}
        logger.info("Response: %s", json.dumps(final_response))
        return final_response

    except KeyError as ke:
        logger.error("Key error: {str(ke)}")
        final_response = {'response': f"Missing required information: {str(ke)}"}
        return final_response

    except Exception as e:
        logger.error("An error occurred: %s", e, exc_info=True)
        final_response = {'response': f"Error: {str(e)}"}
        return final_response

def fetch_files_from_github(repo_name, branch_name):
    api_endpoint = f"https://api.github.com/repos/{repo_name}/contents?ref={branch_name}"

    response = requests.get(api_endpoint, headers=GITHUB_HEADERS, timeout=60)
    response.raise_for_status()

    files_content = ""
    terraform_extensions = ['.tf', '.tfvars']

    for item in response.json():
        if item['type'] == 'file' and any(item['name'].endswith(ext) for ext in terraform_extensions):
            file_response = requests.get(item['download_url'], headers=GITHUB_HEADERS, timeout=60)
            file_response.raise_for_status()
            files_content += f"File: {item['name']}\n"
            files_content += file_response.text + "\n\n"

    logger.info("Fetched repository files content successfully")

    return files_content

def fetch_github_actions_details(logs_url):

    try:
        logger.info(f"Fetching logs from logs_url: {logs_url}")
        logs_response = requests.get(logs_url, headers=GITHUB_HEADERS, timeout=60)
        logs_response.raise_for_status()

        log_content = logs_response.text
        return extract_error_with_context(log_content)

    except requests.exceptions.HTTPError as http_err:
        logger.error("HTTP error occurred while fetching GitHub Actions details: {http_err}")
        raise
    except Exception as e:
        logger.error("Error occurred while fetching GitHub Actions details: {e}")
        raise

def extract_error_with_context(log_content):
    lines = log_content.splitlines()
    for idx, line in enumerate(lines):
        if "error" in line.lower():
            return "\n".join(lines[idx:]).strip()
    return ""

def get_steps_to_remediate(repo_files_content, error_message):

    prompt = f"""
        <task>
        You are an expert in troubleshooting Terraform code issues. Your role is to diagnose the root cause of an error and provide clear, step-by-step instructions for another AI to follow.

        <error_message>
        {error_message}
        </error_message>

        <repo_files_content>
        {repo_files_content}
        </repo_files_content>
        
        <instructions>
        1.  Provide a detailed "Root Cause Analysis" of the error.
        2.  Provide numbered, "Step-by-Step Resolution" instructions.
        3.  **Crucially, do NOT include any corrected code snippets or full code files in your response or how to test it.** Your only output should be the analysis and the instructional steps.
        </instructions>
        </task>
        """

    logger.info("Prompt to get the steps for remediation: %s", prompt)

    steps_to_remediate = invoke_bedrock_model(prompt)
    logger.info("Steps to Remediate: %s", steps_to_remediate)

    return steps_to_remediate

def remediate_code(repo_files_content, steps_to_remediate):

    prompt = f"""
        <task>
        You are an expert automated code modification agent. Your task is to apply a set of instructions to a codebase and return a valid JSON object containing the full, modified files.

        <steps_to_remediate>
        {steps_to_remediate}
        </steps_to_remediate>

        <repo_files_content>
        {repo_files_content}
        </repo_files_content>
        
        <instructions>
        1. Your only source of truth for the original code is the <repo_files_content> section.
        2. Apply only the changes described in the <steps_to_remediate>. If a step includes a code snippet, **ignore the snippet** and use only the text instructions to perform the modification.
        3. The `files` object in your JSON response **MUST contain the COMPLETE content of each modified file**. Do not return just diffs or partial content.
        4. Return a single, valid JSON object enclosed in a `json` markdown code block, as shown in the format below.
        5. Make sure the changes have proper indentation and formatting and don't fail the `terraform validate` command.
        </instructions>
        <output_format>
        ```json
        {{
          "commit_message": "Fix: A short, clear explanation of the fix based on the root cause analysis.",
          "branch_name": "fix/short-branch-name-based-on-root-cause",
          "pr_title": "Fix: A concise title for the pull request.",
          "pr_body": "A detailed description of the pull request, based on the root cause analysis and resolution steps.",
          "files": {{
            "path/to/modified_file_1.tf": "<modified file content as escaped JSON string>",
            "path/to/modified_file_2.tf": "<modified file content as escaped JSON string>"
          }}
        }}
        ```
        </output_format>
        </task>
        """

    logger.info("Prompt for remediation: %s", prompt)

    fixed_code = invoke_bedrock_model(prompt)

    logger.info("Fixed Code: %s", fixed_code)

    return fixed_code

def create_new_branch(fixed_code, repo_name):
    files = {}

    fixed_code_json = json.loads(fixed_code.lstrip("```json").split("```")[0].strip())
    logger.info("Fixed Code after stripping: %s", fixed_code_json)

    new_branch_name = fixed_code_json['branch_name']
    commit_message = fixed_code_json['commit_message']
    pr_title = fixed_code_json['pr_title']
    pr_body = fixed_code_json['pr_body']

    for filename, content in fixed_code_json.get("files").items():
        files[filename] = content.strip()

    logger.info(f"New branch name: {new_branch_name}")
    logger.info(f"Commit Message: {commit_message}")
    logger.info(f"PR Title: {pr_title}")
    logger.info(f"PR body: {pr_body}")
    logger.info(f"Parsed repository path: {repo_name}")

    # Get the base commit SHA
    base_commit = requests.get(
        f"https://api.github.com/repos/{repo_name}/git/refs/heads/main",
        headers=GITHUB_HEADERS
    ).json()["object"]["sha"]

    logger.info(f"Main base_commit sha: {base_commit}")
    # Check if the branch already exists
    if requests.get(
            f"https://api.github.com/repos/{repo_name}/git/refs/heads/{new_branch_name}",
            headers=GITHUB_HEADERS
    ).status_code == 200:
        raise ValueError(f"Branch {new_branch_name} already exists.")

    logger.info(f"Branch not exists so creating: {new_branch_name}")

    # Create the new branch
    response = requests.post(
        f"https://api.github.com/repos/{repo_name}/git/refs",
        json={"ref": f"refs/heads/{new_branch_name}", "sha": base_commit},
        headers=GITHUB_HEADERS
    )

    if response.status_code == 201:
        logger.info(f"Branch created: {new_branch_name}")
    else:
        logger.error("Failed to create branch {new_branch_name}. Response: {response.status_code}, {response.text}")
        raise Exception(f"Failed to create branch {new_branch_name}.")

    # Create blobs for each file
    blobs = []
    for file_name, file_content in files.items():
        blob_payload = {
            "content": base64.b64encode(file_content.encode()).decode(),
            "encoding": "base64"
        }
        blob_sha = requests.post(
            f"https://api.github.com/repos/{repo_name}/git/blobs",
            json=blob_payload,
            headers=GITHUB_HEADERS
        ).json()["sha"]
        blobs.append({"path": file_name, "mode": "100644", "type": "blob", "sha": blob_sha})

    # Create a new tree
    base_tree_sha = requests.get(
        f"https://api.github.com/repos/{repo_name}/git/trees/{base_commit}",
        headers=GITHUB_HEADERS
    ).json()["sha"]
    new_tree_sha = requests.post(
        f"https://api.github.com/repos/{repo_name}/git/trees",
        json={"base_tree": base_tree_sha, "tree": blobs},
        headers=GITHUB_HEADERS
    ).json()["sha"]

    # Create a new commit
    new_commit_sha = requests.post(
        f"https://api.github.com/repos/{repo_name}/git/commits",
        json={"message": commit_message, "tree": new_tree_sha, "parents": [base_commit]},
        headers=GITHUB_HEADERS
    ).json()["sha"]

    # Update the branch reference to point to the new commit
    requests.patch(
        f"https://api.github.com/repos/{repo_name}/git/refs/heads/{new_branch_name}",
        json={"sha": new_commit_sha},
        headers=GITHUB_HEADERS
    )

    create_pull_request(repo_name, new_branch_name, "main", pr_title, pr_body)

    return new_branch_name


def create_pull_request(repo_name, new_branch_name, base_branch, title, body):

    pr_payload = {
        "title": title,
        "body": body,
        "head": new_branch_name,
        "base": base_branch
    }

    response = requests.post(
        f"https://api.github.com/repos/{repo_name}/pulls",
        json=pr_payload,
        headers=GITHUB_HEADERS
    )

    if response.status_code == 201:
        logger.info(f"Pull request created successfully: {response.json().get('html_url')}")
        return response.json().get('html_url')
    else:
        logger.error("Failed to create pull request. Response: {response.status_code}, {response.text}")
        raise Exception("Failed to create pull request.")

def invoke_bedrock_model(prompt):
    try:
        # Set Claude model ID directly
        model_id = "amazon.titan-text-premier-v1:0"
        body = json.dumps({
            "inputText": prompt,
            "textGenerationConfig": {
                "temperature": 0.7,
                "topP": 0.9,
                "maxTokenCount": 3072,
                "stopSequences": []
            }
        })

        response = bedrock.invoke_model(
            modelId=model_id,
            body=body,
            accept="application/json",
            contentType="application/json"
        )

        response_body = json.loads(response.get('body').read())
        return response_body['results'][0]['outputText']

    except Exception as e:
        logger.error("Error invoking Bedrock model: {e}")
        raise