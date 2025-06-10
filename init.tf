terraform {
  backend "s3" {
    region       = "us-east-1"
    bucket       = "vinod-terraform-test-bucket"
    key          = "merlion/dev/troubleshoot-terraform"
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-west-2"
}