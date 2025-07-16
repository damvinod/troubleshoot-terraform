resource "aws_s3_bucket" "bucket_test" {
  bucket = "test-121212121212121212121212121212"
  acl = "private"
}

resource "aws_s3_bucket_versioning" "bucket_test" {
  bucket = aws_s3_bucket.bucket_test.bucket
}