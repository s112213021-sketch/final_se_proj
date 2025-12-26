import bcrypt

def hash_password(password: str) -> str:
    """將密碼使用 bcrypt 進行雜湊"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """驗證密碼是否匹配其雜湊值"""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())

# 測試用
if __name__ == "__main__":
    # 測試雜湊
    test_pw = "123456"
    hashed = hash_password(test_pw)
    print(f"Password hash: {hashed}")
    
    # 測試驗證
    assert verify_password(test_pw, hashed)
    print("密碼雜湊和驗證功能正常")