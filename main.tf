resource "aws_s3_bucket" "example" {
  bucket = "unique-test-bucket"

  tags = {
    Name        = "My bucket"
    Environment = "Dev"
  }
}