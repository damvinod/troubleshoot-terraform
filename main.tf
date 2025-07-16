terraform {
  backend "s3" {
    region       = "ap-southeast-1"
    bucket       = "vinod-terraform-test-bucket"
    key          = "merlion/dev/troubleshoot-terraform"
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.3.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "archive_file" "lambda_zip_archive" {
  type        = "zip"
  source_dir  = "lambda"
  output_path = "lambda_function.zip"
}

resource "aws_iam_role" "lambda_exec_role" {
  name = "my-lambda-exec-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect   = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_logging" {
  name = "my-lambda-logging-policy"
  role = aws_iam_role.lambda_exec_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "bedrock:InvokeModel",
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "my_lambda_function" {
  function_name    = "terraform-troubleshoot-lambda"
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.13"
  role             = aws_iam_role.lambda_exec_role.arn
  filename         = data.archive_file.lambda_zip_archive.output_path
  source_code_hash = data.archive_file.lambda_zip_archive.output_base64sha256
  timeout          = 60
  memory_size      = 512

  environment {
    variables = {
      GITHUB_PAT = var.github_pat
    }
  }
}

resource "aws_s3_bucket" "bucket_test" {
  bucket = "test-12121212121212121212121212121212"
  acl    = "private"
}

output "s3_bucket_name" {
  value = aws_s3_bucket.bucket_test[0].bucket_domain_name
}

variables {
  variable "github_pat" {
    type      = string
    sensitive = true
  }
}