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
        logger.error(f"Key error: {str(ke)}")
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
        logger.error(f"HTTP error occurred while fetching GitHub Actions details: {http_err}")
        raise
    except Exception as e:
        logger.error(f"Error occurred while fetching GitHub Actions details: {e}")
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
        You are an expert in troubleshooting Terraform code issues. Your goal is to provide a complete solution that not only fixes the immediate error but also aligns with industry best practices.
        
        You will be given a Terraform error message and the content of the relevant files from a Git repository.
        </task>

        <error_message>
        {error_message}
        </error_message>

        <repo_files_content>
        {repo_files_content}
        </repo_files_content>
        
        <instructions>
        Carefully analyze the error message in the context of the provided repository files and generate a response with the following markdown structure. Do not add any other commentary.
    
        ### Root Cause Analysis
        Explain what the error message means and the specific reason it is occurring based on the provided code.
    
        ### Step-by-Step Resolution
        Provide clear, numbered steps to fix the issue. Reference file paths and line numbers where possible.
    
        ### Corrected Code Snippet
        Show the exact code block(s) that need to be changed, presenting the corrected version. Use ```hcl code fences for the snippets.
        </instructions>
        """

    logger.info("Prompt to get the steps for remediation: %s", prompt)

    steps_to_remediate = invoke_bedrock_model(prompt)
    logger.info("Steps to Remediate: %s", steps_to_remediate)

    return steps_to_remediate

def remediate_code(repo_files_content, steps_to_remediate):

    prompt = f"""
        <task>
        You are an expert automated code modification agent. Your task is to implement changes in Terraform code based on a provided remediation plan and return valid JSON object. You MUST NOT output any other text or explanations.
        </task>
        
        <steps_to_remediate>
        {steps_to_remediate}
        </steps_to_remediate>

        <repo_files_content>
        {repo_files_content}
        </repo_files_content>
        
        <instructions>
        1.  Carefully follow the instructions in `<steps_to_remediate>`.
        2.  Apply the fixes to the code provided in `<repo_files_content>`.
        3.  Generate a single, valid JSON object as your final response.
        4.  **Your entire response must ONLY be the JSON object and nothing else.**
        5.  The `files` object in the JSON should only contain files that were actually modified. Do not include unchanged files.
        6.  The final JSON object must follow this exact structure:
            ```json
            {{
              "files": {{
                "path/to/modified_file_1.tf": "<the entire content of the modified file as a valid, escaped JSON string and do not use backticks (` `)>"
                "path/to/modified_file_2.tf": "<the entire content of the modified file as a valid, escaped JSON string and do not use backticks (` `)>"
              }},
              "commit_message": "Fix: A short, clear explanation of the fix based on the root cause analysis.",
              "branch_name": "fix/short-branch-name-with-unique-10-digit-number",
              "pr_title": "Fix: A concise title for the pull request.",
              "pr_body": "A detailed description for the pull request, based on the Root Cause Analysis and Step-by-Step Resolution."
            }}
            ```
        </instructions>
        """

    fixed_code = invoke_bedrock_model(prompt)

    logger.info("Fixed Code: %s", fixed_code)

    return fixed_code

def create_new_branch(fixed_code, repo_name):
    files = {}

    fixed_code_json = json.loads(fixed_code.lstrip("```json").split("```")[0].strip())
    logger.info("Fixed Code after stripping: %s", fixed_code_json)

    new_branch_name = fixed_code_json.get("branch_name")
    commit_message = fixed_code_json.get("commit_message")
    pr_title = fixed_code_json.get("pr_title")
    pr_body = fixed_code_json.get("pr_body")

    for filename, content in fixed_code_json.get("files").items():
        files[filename] = content.strip()

    # Parse repository path
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
        logger.error(f"Failed to create branch {new_branch_name}. Response: {response.status_code}, {response.text}")
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
        logger.error(f"Failed to create pull request. Response: {response.status_code}, {response.text}")
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
        logger.error(f"Error invoking Bedrock model: {e}")
        raise