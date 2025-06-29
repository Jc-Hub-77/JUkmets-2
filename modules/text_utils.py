# modules/text_utils.py

def escape_md(text: str, version: int = 2) -> str:
    """
    Helper function to escape telegram markdown characters.
    Taken from: https://github.com/python-telegram-bot/python-telegram-bot/blob/v13.14/telegram/utils/helpers.py#L19 टेलीग्राम/utils/helpers.py
    Adjusted for simple string input.

    Args:
        text: The text to escape.
        version: Markdown version (1 or 2). Default is 2.
    Returns:
        The escaped text.
    """
    if version == 1:
        escape_chars = r'_*`['
    elif version == 2:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
    else:
        raise ValueError('Markdown version must be 1 or 2.')

    return "".join(['\\' + char if char in escape_chars else char for char in str(text)])

if __name__ == '__main__':
    test_string_v1 = "This is a _test_ *string* with `code` and [link](url)."
    escaped_v1 = escape_md(test_string_v1, 1)
    print(f"Original V1: {test_string_v1}")
    print(f"Escaped V1:  {escaped_v1}")

    test_string_v2 = "This is a _test_ *string* with `code`, [link](url), ~strike~, and some.special-chars! > hash#"
    escaped_v2 = escape_md(test_string_v2, 2)
    print(f"\nOriginal V2: {test_string_v2}")
    print(f"Escaped V2:  {escaped_v2}")

    test_url = "http://example.com/query?a=b&c=d"
    print(f"\nOriginal URL: {test_url}")
    print(f"Escaped URL V2: {escape_md(test_url)}")

    test_code_block = "```python\nprint('hello')\n```"
    print(f"\nOriginal Code Block: {test_code_block}")
    print(f"Escaped Code Block V2: {escape_md(test_code_block)}") # Note: `escape_md` is not for code blocks themselves but content within them or general text.
                                                               # Code blocks have their own way of being handled by clients.
                                                               # This function is for escaping inline markdowns.

    just_dots = "..."
    print(f"\nOriginal dots: {just_dots}")
    print(f"Escaped dots V2: {escape_md(just_dots)}")

    payment_addr = "TX7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    print(f"\nOriginal Addr: {payment_addr}")
    print(f"Escaped Addr V2: {escape_md(payment_addr)}")

    amount_str = "0.00123000"
    print(f"\nOriginal Amount: {amount_str}")
    print(f"Escaped Amount V2: {escape_md(amount_str)}")
