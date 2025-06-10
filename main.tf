resource "aws_s3_bucket" "example" {
  bucket = "new-test-bucket"

  tags = {
    Name        = "My bucket"
    Environment = "Dev"
  }
}