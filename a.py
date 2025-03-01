import requests
import re
import time

API_URL = "https://zh.wikipedia.org/w/api.php"

# 請填入您自己的機器人帳號/或有權限的帳號與密碼
USERNAME = ""
PASSWORD = ""

HEADERS = {
    "User-Agent": "OnlyStubBot/2.0 (https://zh.wikipedia.org/; YourBotUsername)"
}

# 常見消歧義模板清單
DISAMBIG_TEMPLATES = [
    '{{disambig',
    '{{Disambiguation',
    '{{Disamb',
    '{{Dab',
    '{{消歧义'
]

def login(session: requests.Session) -> bool:
    """
    使用帳號密碼登入 MediaWiki（以 BotPassword 或普通帳號密碼方式）。
    回傳是否登入成功。
    """
    # (1) 取得 Login Token
    r1 = session.get(API_URL, params={
        'action': 'query',
        'meta': 'tokens',
        'type': 'login',
        'format': 'json'
    }, headers=HEADERS)
    data1 = r1.json()
    login_token = data1['query']['tokens']['logintoken']

    # (2) 帶 login token 進行登入
    r2 = session.post(API_URL, data={
        'action': 'login',
        'lgname': USERNAME,
        'lgpassword': PASSWORD,
        'lgtoken': login_token,
        'format': 'json'
    }, headers=HEADERS)
    data2 = r2.json()

    if data2.get('login', {}).get('result') == 'Success':
        print(f"已成功登入帳號：{USERNAME}")
        return True
    else:
        print("登入失敗：", data2)
        return False

def get_csrf_token(session: requests.Session) -> str:
    """
    取得 CSRF 編輯 token
    """
    r = session.get(API_URL, params={
        'action': 'query',
        'meta': 'tokens',
        'format': 'json'
    }, headers=HEADERS)
    data = r.json()
    return data['query']['tokens']['csrftoken']

def remove_some_markup(text: str) -> str:
    """
    粗略移除特定 Wiki 標記與 HTML 標籤，用於字數計算。
    """
    # 移除註解、HTML標籤、<ref>...</ref>
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref.*?>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<.*?>', '', text, flags=re.DOTALL)

    # 移除模板 {{...}}
    text = re.sub(r'\{\{.*?\}\}', '', text, flags=re.DOTALL)

    # 移除 [[Category:...]]、[[File:...]]、[[檔案:...]] 等
    text = re.sub(r'\[\[(?:Category|分類|File|檔案):.*?\]\]', '', text, flags=re.IGNORECASE)

    # 將 [[連結|顯示文字]] 替換成 顯示文字
    text = re.sub(r'\[\[([^|\]]*\|)?([^\]]+)\]\]', r'\2', text)

    return text

def count_effective_length(text: str) -> float:
    """
    根據需求：
      - 中文 / 中標點 => +1
      - 單個英數 => +0.5
      - 外文單詞(≥2字母連續英文) => +2
      - 其餘符號 => +1
    """
    tokens = re.split(r'(\s+|[,\.\!\?\(\)；;，。、「」…])', text)
    total_score = 0.0

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        
        # 若 token 全為中文 (含中日韓基礎統一表意文字)
        m_cjk = re.findall(r'[\u4e00-\u9fff]', token)
        if m_cjk and len(m_cjk) == len(token):
            total_score += len(token)
            continue
        
        # 外文單詞(≥2字母)
        if re.match(r'^[A-Za-z]+$', token):
            if len(token) >= 2:
                total_score += 2.0
            else:
                total_score += 0.5
            continue
        
        # 純數字 => 每個數字 +0.5
        if re.match(r'^[0-9]+$', token):
            total_score += 0.5 * len(token)
            continue
        
        # 英數混合(≥2字) => 視為外文單詞 => +2
        if re.match(r'^[A-Za-z0-9]+$', token):
            if len(token) >= 2:
                total_score += 2.0
            else:
                total_score += 0.5
            continue
        
        # 其餘當作符號 => +1 * (字符數)
        total_score += len(token)
    
    return total_score

def is_spam_or_test_page(wikitext: str) -> bool:
    """
    簡易檢查是否疑似測試、廣告頁面，可再擴充
    """
    lower_text = wikitext.lower()
    keywords = ["廣告", "測試", "test", "這是一個測試", "贊助", "qq", "wechat"]
    for kw in keywords:
        if kw in lower_text:
            return True
    return False

def edit_page(session: requests.Session, csrf_token: str, title: str, new_text: str, summary: str) -> bool:
    """
    寫入新的頁面內容
    """
    r = session.post(API_URL, data={
        'action': 'edit',
        'title': title,
        'text': new_text,
        'summary': summary,
        'token': csrf_token,
        'format': 'json'
    }, headers=HEADERS)
    resp = r.json()
    if resp.get('edit', {}).get('result') == 'Success':
        return True
    else:
        print("編輯失敗：", resp)
        return False

def main():
    S = requests.Session()
    
    # 1) 登入
    if not login(S):
        print("登入失敗，程式結束。")
        return
    
    # 2) 取得 CSRF token
    csrf_token = get_csrf_token(S)
    
    # 3) 分批遍歷 allpages，直到無更多頁面可繼續
    apcontinue = None
    ap_limit = 50  # 每次抓取的頁數，可依需求調整
    
    while True:
        params_allpages = {
            'action': 'query',
            'list': 'allpages',
            'apnamespace': '0',               # 條目名字空間
            'apfilterredir': 'nonredirects',  # 排除重定向
            'aplimit': ap_limit,
            'format': 'json'
        }
        if apcontinue:
            params_allpages['apcontinue'] = apcontinue
        
        # 抓取一批頁面
        r_allpages = S.get(API_URL, params=params_allpages, headers=HEADERS)
        data_ap = r_allpages.json()
        
        # 解析頁面列表
        pages = data_ap.get('query', {}).get('allpages', [])
        if not pages:
            print("本批沒有取得任何條目，或遍歷結束。")
            break
        
        for p in pages:
            pageid = p['pageid']
            title = p['title']
            
            # 取得該頁面原始碼
            params_content = {
                'action': 'query',
                'prop': 'revisions',
                'rvprop': 'content',
                'rvslots': 'main',
                'pageids': pageid,
                'format': 'json'
            }
            resp_content = S.get(API_URL, params=params_content, headers=HEADERS)
            jdata = resp_content.json()
            
            pages_dict = jdata.get('query', {}).get('pages', {})
            page_obj = pages_dict.get(str(pageid), {})
            revs = page_obj.get('revisions', [])
            
            if not revs:
                print(f"條目「{title}」沒有內容，跳過。")
                continue
            
            wikitext = revs[0]['slots']['main'].get('*', '')
            lower_text = wikitext.lower()
            
            # 跳過已掛 stub/substub
            if '{{stub' in lower_text or '{{substub' in lower_text:
                print(f"條目「{title}」已掛 stub 或 substub，跳過。")
                continue
            
            # 跳過含消歧義模板
            if any(tpl.lower() in lower_text for tpl in DISAMBIG_TEMPLATES):
                print(f"條目「{title}」含消歧義模板，跳過。")
                continue
            
            # 跳過疑似測試/廣告頁
            if is_spam_or_test_page(wikitext):
                print(f"條目「{title}」疑似測試或廣告頁，跳過。")
                continue
            
            # 計算字數
            cleaned = remove_some_markup(wikitext)
            length_score = count_effective_length(cleaned)
            
            # 若 < 200，掛{{stub}}
            if length_score < 200:
                new_text = wikitext.strip() + "\n\n{{stub}}"
                summary_msg = f"Bot: 自動標示此條目為小作品 (字數={length_score:.1f})"
                
                if new_text != wikitext:
                    ok = edit_page(S, csrf_token, title, new_text, summary_msg)
                    if ok:
                        print(f"條目「{title}」掛{{stub}}成功。（字數={length_score:.1f}）")
                    else:
                        print(f"條目「{title}」掛{{stub}}失敗。")
            else:
                print(f"條目「{title}」字數≥200 ({length_score:.1f})，不處理。")
            
            # (選擇性) 節流，以免頻繁編輯引發伺服器壓力
            # time.sleep(1)
        
        # 檢查是否有下一批
        if 'continue' in data_ap:
            apcontinue = data_ap['continue']['apcontinue']
            print(f"繼續下一批，apcontinue = {apcontinue}")
        else:
            print("已無後續頁面，遍歷結束。")
            break
    
    print("程式已執行完畢。")

if __name__ == "__main__":
    main()
