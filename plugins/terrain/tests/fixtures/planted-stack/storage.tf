resource "aws_s3_bucket" "data" {
  bucket = "app-data-${var.environment}"
  acl    = "private"

  versioning {
    enabled = true
  }
}

resource "aws_s3_bucket" "uploads" {
  bucket = "app-uploads-${var.environment}"
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = false
  ignore_public_acls      = true
  restrict_public_buckets = false
}
