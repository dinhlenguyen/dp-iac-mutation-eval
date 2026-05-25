resource "aws_instance" "demo" {
  ami           = "ami-12345678"
  instance_type = "t3.micro"

  tags = {
    Name = "localstack-demo-instance"
    env  = "prod"
  }
}