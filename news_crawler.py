import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import schedule
import time
import os
from dotenv import load_dotenv

load_dotenv()

NEWS_SOURCES = [
    {
        'name': 'BBC News',
        'url': 'https://www.bbc.com/news',
        'parser': 'bbc'
    },
    {
        'name': 'CNN',
        'url': 'https://www.cnn.com',
        'parser': 'cnn'
    },
    {
        'name': 'Reuters',
        'url': 'https://www.reuters.com',
        'parser': 'reuters'
    }
]

def fetch_news(url, parser_type):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        if parser_type == 'bbc':
            return parse_bbc(soup)
        elif parser_type == 'cnn':
            return parse_cnn(soup)
        elif parser_type == 'reuters':
            return parse_reuters(soup)
        else:
            return []
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []

def parse_bbc(soup):
    news_items = []
    articles = soup.find_all('article', {'class': 'gs-c-promo'})
    for article in articles[:3]:
        try:
            title = article.find('h3', {'class': 'gs-c-promo-heading__title'}).get_text(strip=True)
            summary = article.find('p', {'class': 'gs-c-promo-summary'}).get_text(strip=True) if article.find('p', {'class': 'gs-c-promo-summary'}) else ''
            link = 'https://www.bbc.com' + article.find('a')['href']
            news_items.append({
                'title': title,
                'summary': summary,
                'link': link,
                'source': 'BBC News'
            })
        except Exception as e:
            continue
    return news_items

def parse_cnn(soup):
    news_items = []
    articles = soup.find_all('div', {'class': 'container__headline'})
    for article in articles[:3]:
        try:
            title = article.get_text(strip=True)
            link = 'https://www.cnn.com' + article.find('a')['href']
            news_items.append({
                'title': title,
                'summary': '',
                'link': link,
                'source': 'CNN'
            })
        except Exception as e:
            continue
    return news_items

def parse_reuters(soup):
    news_items = []
    articles = soup.find_all('article', {'class': 'story'})
    for article in articles[:3]:
        try:
            title = article.find('h3').get_text(strip=True) if article.find('h3') else article.find('h2').get_text(strip=True)
            summary = article.find('p').get_text(strip=True) if article.find('p') else ''
            link = 'https://www.reuters.com' + article.find('a')['href']
            news_items.append({
                'title': title,
                'summary': summary,
                'link': link,
                'source': 'Reuters'
            })
        except Exception as e:
            continue
    return news_items

def send_email(news_list):
    sender_email = os.getenv('EMAIL_SENDER')
    receiver_email = os.getenv('EMAIL_RECEIVER')
    password = os.getenv('EMAIL_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))

    subject = "每日国际新闻摘要"
    
    body = f"📰 每日国际新闻摘要 ({time.strftime('%Y-%m-%d')})\n\n"
    body += "="*50 + "\n\n"
    
    for i, news in enumerate(news_list, 1):
        body += f"{i}. 【{news['source']}】\n"
        body += f"标题: {news['title']}\n"
        if news['summary']:
            body += f"摘要: {news['summary']}\n"
        body += f"链接: {news['link']}\n"
        body += "\n" + "-"*50 + "\n\n"
    
    body += "---\n这是自动发送的每日新闻摘要邮件"

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, password)
            server.sendmail(sender_email, receiver_email, msg.as_string())
        print("邮件发送成功")
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False

def crawl_and_send():
    print(f"开始爬取新闻... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    all_news = []
    
    for source in NEWS_SOURCES:
        news = fetch_news(source['url'], source['parser'])
        all_news.extend(news)
    
    if len(all_news) >= 3:
        selected_news = sorted(all_news, key=lambda x: x['source'])[:3]
        send_email(selected_news)
    else:
        print(f"只获取到 {len(all_news)} 条新闻，跳过发送")

def main():
    crawl_and_send()
    
    schedule.every().day.at("08:00").do(crawl_and_send)
    
    print("新闻爬虫服务已启动，每天早上8点自动发送新闻摘要...")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()