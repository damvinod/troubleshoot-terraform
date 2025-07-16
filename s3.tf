resource "aws_s3_bucket" "bucket_test" {
  bucket = "test-terraform-bucket-terraform-1234567890"
  acls   = "private"
}

resource "aws_s3_bucket_versioning" "bucket_test" {
  bucket = aws_s3_bucket.bucket_test.bucket
}