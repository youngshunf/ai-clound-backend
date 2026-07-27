from typing import Any, Literal

def text_captcha(num: int = ...) -> str: ...
def img_captcha(
    *,
    code_num: int = ...,
    width: int = ...,
    height: int = ...,
    font_type: str = ...,
    font_size: int = ...,
    draw_lines: bool = ...,
    lines_num: int = ...,
    draw_points: bool = ...,
    points_density: int = ...,
    img_type: str = ...,
    img_byte: Literal['file', 'bytesio', 'base64'] = ...,
) -> tuple[Any, str]: ...
