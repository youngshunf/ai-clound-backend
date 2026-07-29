from urllib.parse import quote


def build_amqp_dsn(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    vhost: str,
) -> str:
    """构造完整 AMQP DSN，仅供建立连接，禁止写入日志。"""
    encoded_username = quote(username, safe='')
    encoded_password = quote(password, safe='')
    encoded_vhost = quote(vhost, safe='')
    return (
        f'amqp://{encoded_username}:{encoded_password}'
        f'@{host}:{port}/{encoded_vhost}'
    )


def describe_rabbitmq_endpoint(*, host: str, port: int, vhost: str) -> str:
    """返回不含账号、密码和完整 DSN 的 RabbitMQ 端点描述。"""
    return f'host={host} port={port} vhost={vhost}'
