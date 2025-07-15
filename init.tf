terraform {
  backend "s3" {
    region       = "ap-southeast-1"
    bucket       = "vinod-terraform-test-bucket"
    key          = "merlion/dev/troubleshoot-terraform"
    use_lockfile = true
  }
}

provider "aws" {
  region = "ap-southeast-1"
}