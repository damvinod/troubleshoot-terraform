import json
import logging
import os
import boto3
import requests
import tempfile
import zipfile
import base64
from urllib.parse import urlparse

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 clients
bedrock = boto3.client(service_name='bedrock-runtime')

def fetch_github_actions_details(repo_url, branch_name):

    repo_path = repo_url.split("https://github.com/")[-1].rstrip('/')
    api_endpoint = f"https://api.github.com/repos/{repo_path}/actions/runs?branch={branch_name}"

    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {os.environ.get("GITHUB_PAT")}',
        'User-Agent': 'lambda_function'
    }

    try:
        response = requests.get(api_endpoint, headers=headers, timeout=60)
        response.raise_for_status()
        workflow_runs = response.json().get('workflow_runs', [])

        if not workflow_runs:
            logger.info("No workflow runs found for the specified branch.")
            return "No workflow runs found."

        latest_run = workflow_runs[0]  # Get the latest workflow run
        if latest_run.get("conclusion") == "failure":
            logs_url = latest_run.get("logs_url")
            if logs_url:
                logger.info(f"Fetching logs from logs_url: {logs_url}")
                logs_response = requests.get(logs_url, headers=headers, timeout=60)
                logs_response.raise_for_status()

                # Create a temporary directory for the ZIP file
                with tempfile.TemporaryDirectory() as temp_dir:
                    zip_path = os.path.join(temp_dir, "logs.zip")

                    # Save the ZIP content
                    with open(zip_path, 'wb') as f:
                        f.write(logs_response.content)

                    # Extract and read the terraform log file
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        try:
                            with zip_ref.open('0_terraform.txt') as tf_log:
                                log_content = tf_log.read().decode('utf-8')
                                return extract_error_with_context(log_content)
                        except KeyError:
                            logger.warning("0_terraform.txt not found in logs zip")
                            return "Terraform log file not found in workflow artifacts."

        return "Latest workflow run did not fail."

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error occurred while fetching GitHub Actions details: {http_err}")
        raise
    except Exception as e:
        logger.error(f"Error occurred while fetching GitHub Actions details: {e}")
        raise

def extract_error_with_context(log_content):
    error_logs = []
    for line in log_content.splitlines():
        if "error" in line.lower():
            error_logs.append(line.strip())
    return "\n".join(error_logs)

def fetch_files_from_github(repo_url, branch_name):
    # Fetch repository contents using the GitHub API
    repo_path = repo_url.split("https://github.com/")[-1].rstrip('/')
    api_endpoint = f"https://api.github.com/repos/{repo_path}/contents?ref={branch_name}"

    headers = {}

    response = requests.get(api_endpoint, headers=headers, timeout=60)
    response.raise_for_status()

    files_content = ""
    terraform_extensions = ['.tf', '.tfvars']

    for item in response.json():
        if item['type'] == 'file' and any(item['name'].endswith(ext) for ext in terraform_extensions):
            file_response = requests.get(item['download_url'], headers=headers, timeout=60)
            file_response.raise_for_status()
            files_content += f"File: {item['name']}\n"
            files_content += file_response.text + "\n\n"

    return files_content

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

def remediate_code(code, steps_to_remediate):
    # Updated prompt to ask for only code without any extra comments, delimiters, or explanations

    prompt = f"""
        <task>
        You are an expert in fixing Terraform code issues. Below are the steps to fix the issue and the contents of a Git repository.

        <steps_to_remediate>
        {steps_to_remediate}
        </steps_to_remediate>

        <repo_files_content>
        {code}
        </repo_files_content>
        
        <instructions>
        Fix the identified issues in the code and return only the files which are modified in same format.
        </instructions>
        <output_format>
        Return a JSON object with the following fields (no other text or explanations):
        {{
          "files": {{
            "path/to/modified_file_1.tf": "<modified file contents>",
            "path/to/modified_file_2.tf": "<modified file contents>"
          }},
          "commit_message": "Short and clear explanation of the fix",
          "branch_name": "descriptive_branch_name",
          "pr_title": "title_for_pull_request",
          "pr_body": "description_for_pull_request"
        }}
        </output_format>
        </task>
        """

    fixed_code = invoke_bedrock_model(prompt)

    logger.info("Fixed Code: %s", fixed_code)

    return fixed_code

def create_new_branch(fixed_code, repo_url):
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
    repo_path = urlparse(repo_url).path.strip('/')
    logger.info(f"Parsed repository path: {repo_path}")
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {os.environ.get("GITHUB_PAT")}'
    }

    # Get the base commit SHA
    base_commit = requests.get(
        f"https://api.github.com/repos/{repo_path}/git/refs/heads/main",
        headers=headers
    ).json()["object"]["sha"]

    logger.info(f"Main base_commit sha: {base_commit}")
    # Check if the branch already exists
    if requests.get(
            f"https://api.github.com/repos/{repo_path}/git/refs/heads/{new_branch_name}",
            headers=headers
    ).status_code == 200:
        raise ValueError(f"Branch {new_branch_name} already exists.")

    logger.info(f"Branch not exists so creating: {new_branch_name}")

    # Create the new branch
    response = requests.post(
        f"https://api.github.com/repos/{repo_path}/git/refs",
        json={"ref": f"refs/heads/{new_branch_name}", "sha": base_commit},
        headers=headers
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
            f"https://api.github.com/repos/{repo_path}/git/blobs",
            json=blob_payload,
            headers=headers
        ).json()["sha"]
        blobs.append({"path": file_name, "mode": "100644", "type": "blob", "sha": blob_sha})

    # Create a new tree
    base_tree_sha = requests.get(
        f"https://api.github.com/repos/{repo_path}/git/trees/{base_commit}",
        headers=headers
    ).json()["sha"]
    new_tree_sha = requests.post(
        f"https://api.github.com/repos/{repo_path}/git/trees",
        json={"base_tree": base_tree_sha, "tree": blobs},
        headers=headers
    ).json()["sha"]

    # Create a new commit
    new_commit_sha = requests.post(
        f"https://api.github.com/repos/{repo_path}/git/commits",
        json={"message": commit_message, "tree": new_tree_sha, "parents": [base_commit]},
        headers=headers
    ).json()["sha"]

    # Update the branch reference to point to the new commit
    requests.patch(
        f"https://api.github.com/repos/{repo_path}/git/refs/heads/{new_branch_name}",
        json={"sha": new_commit_sha},
        headers=headers
    )

    create_pull_request(repo_url, new_branch_name, "main", pr_title, pr_body)

    return new_branch_name


def create_pull_request(repo_url, new_branch_name, base_branch, title, body):
    repo_path = urlparse(repo_url).path.strip('/')
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {os.environ.get("GITHUB_PAT")}'
    }

    pr_payload = {
        "title": title,
        "body": body,
        "head": new_branch_name,
        "base": base_branch
    }

    response = requests.post(
        f"https://api.github.com/repos/{repo_path}/pulls",
        json=pr_payload,
        headers=headers
    )

    if response.status_code == 201:
        logger.info(f"Pull request created successfully: {response.json().get('html_url')}")
        return response.json().get('html_url')
    else:
        logger.error(f"Failed to create pull request. Response: {response.status_code}, {response.text}")
        raise Exception("Failed to create pull request.")

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    try:
        workspace_url = event['workspace_url']
        repo_url = event['repo_url']
        branch_name = event['branch_name']
        actionGroup = event['actionGroup']
        function = event['function']

        repo_files_content = ""
        error_message = None

        if repo_url:
            # Fetch the repo content using the GitHub API
            repo_files_content = fetch_files_from_github(repo_url, branch_name)
            logger.info("Fetched repository files content successfully")

        # Get the latest or specified run error from the Terraform workspace
        error_message = fetch_github_actions_details(repo_url, branch_name)

        specific_use_case = f"""
        - If the error is with respect to service control policies or resource based policies then inform the user to contact Security team (abc-security@abc.com) as it is a limitation. DO NOT include any other information.
        - If the error is with respect to S3 bucket creation or VPC resource creation, inform the user to contact Platform team (abc-platform@abc.com) as it is a limitation. DO NOT include any other information.
        """

        print(f'error: {error_message}')
        #print(f'repo: {repo_files_content}')
        if error_message is None and repo_files_content == '':
            raise KeyError("Neither 'files_content' nor 'error_message' were found in the response from Lambda 2")

        # Construct the prompt for the Bedrock model
        prompt = f"""
        <task>
        You are an expert in troubleshooting Terraform code issues. Below is the full log of GitHub actions, identify the error_message from the logs and use the contents from a Git repository.

        <error_message>
        {error_message}
        </error_message>

        <repo_files_content>
        {repo_files_content}
        </repo_files_content>
        
        <instructions>
        Provide step-by-step instructions on how to resolve the error by looking into error_message and take into account the repo_files_content while suggesting the fix. Ensure that the troubleshooting steps are provided aligning to {specific_use_case}.
        </instructions>
        </task>
        """
        print(f"prompt: {prompt}")
        # Invoke Bedrock model to get troubleshooting steps
        troubleshooting_steps = invoke_bedrock_model(prompt)
        logger.info("Generated troubleshooting steps: %s", troubleshooting_steps)

        responseBody = {
            "TEXT": {
                "body": troubleshooting_steps
            }
        }

        # Prepare the action response
        action_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                'responseBody': responseBody
            }
        }

        # Final response structure expected by Bedrock agent
        final_response = {'response': action_response, 'messageVersion': event['messageVersion']}
        logger.info("Response: %s", json.dumps(final_response))

        fixed_code = remediate_code(repo_files_content, troubleshooting_steps)
        create_new_branch(fixed_code, repo_url)

        return final_response

    except KeyError as ke:
        logger.error(f"Key error: {str(ke)}")
        responseBody = {
            "TEXT": {
                "body": f"Missing required information: {str(ke)}"
            }
        }
        action_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                'responseBody': responseBody
            }
        }
        final_response = {'response': action_response, 'messageVersion': event['messageVersion']}
        return final_response

    except Exception as e:
        logger.error("An error occurred: %s", e, exc_info=True)
        responseBody = {
            "TEXT": {
                "body": f"Error: {str(e)}"
            }
        }
        action_response = {
            'actionGroup': actionGroup,
            'function': function,
            'functionResponse': {
                'responseBody': responseBody
            }
        }
        final_response = {'response': action_response, 'messageVersion': event['messageVersion']}
        return final_response