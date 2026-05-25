resource "aws_instance" "demo" {
  ami           = var.ami_id
  instance_type = "t3.micro"

  tags = {
    Name = "localstack-demo-instance"
    env  = "prod"
  }
}