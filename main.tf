resource "aws_s3_bucket" "example" {
  bucket = "test"

  tags = {
    Name        = "My bucket"
    Environment = "Dev"
  }
}