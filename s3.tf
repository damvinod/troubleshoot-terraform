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

resource "aws_s3_bucket" "bucket_test" {
  bucket = "test-terraform-bucket-terraform-1234567890"
  acl = "private"
}