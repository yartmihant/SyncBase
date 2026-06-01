def format_date(date_str):
    """Утилита для форматирования дат"""
    return date_str.replace("-", "/")

def validate_email(email):
    """Простая проверка email"""
    return "@" in email and "." in email
