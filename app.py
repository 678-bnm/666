from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import schedule
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from threading import Thread
import feedparser

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'news_crawler_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///news.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class NewsItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    summary_zh = db.Column(db.Text)
    summary_en = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    link = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(100), nullable=False)
    published_at = db.Column(db.String(100))   # 发布日期字符串
    crawled_at = db.Column(db.DateTime, default=datetime.now)


RSS_SOURCES = [
    {'name': 'BBC News', 'url': 'https://feeds.bbci.co.uk/news/world/rss.xml'},
    {'name': 'NPR News', 'url': 'https://feeds.npr.org/1001/rss.xml'},
]

# 爬取状态（供前端轮询）
crawl_status = {'running': False, 'step': '', 'done': False}


def get_proxies():
    proxy_host = os.getenv('PROXY_HOST', '127.0.0.1')
    proxy_port = os.getenv('PROXY_PORT', '7890')
    return {
        'http': f'http://{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_host}:{proxy_port}'
    }


def summarize_in_chinese(title, summary):
    api_key = os.getenv('GROQ_API_KEY')
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{
                    "role": "user",
                    "content": f"请用中文总结以下新闻，200字以内，要把核心内容说清楚：\n标题：{title}\n内容：{summary}"
                }]
            },
            proxies=get_proxies(),
            timeout=30
        )
        data = response.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"翻译失败: {e}")
        try:
            print(f"返回内容: {response.text}")
        except:
            pass
        return ''


def extract_image(entry):
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0].get('url', '')
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if 'image' in enc.get('type', ''):
                return enc.get('href', '')
    summary_raw = entry.get('summary', '')
    soup = BeautifulSoup(summary_raw, 'html.parser')
    img = soup.find('img')
    if img:
        return img.get('src', '')
    return ''

def fetch_og_image(url):
    """抓取文章页面的 og:image 作为备用图片"""
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                               proxies=get_proxies(), timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og:
            return og.get('content', '')
    except:
        pass
    return ''

def extract_published(entry):
    try:
        t = entry.get('published_parsed') or entry.get('updated_parsed')
        if t:
            return time.strftime('%Y-%m-%d', t)
    except:
        pass
    return datetime.now().strftime('%Y-%m-%d')


def fetch_news_via_rss(url, source_name):
    global crawl_status
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        crawl_status['step'] = f'正在连接 {source_name}…'
        response = requests.get(url, headers=headers, timeout=30, proxies=get_proxies())
        response.raise_for_status()
        feed = feedparser.parse(response.text)

        news_items = []
        for entry in feed.entries[:5]:
            title = entry.get('title', '')
            summary_raw = entry.get('summary', '')
            link = entry.get('link', '')
            image_url = extract_image(entry)
            if not image_url:  # 加这两行
                image_url = fetch_og_image(link)
            published_at = extract_published(entry)

            soup = BeautifulSoup(summary_raw, 'html.parser')
            summary_en = soup.get_text(strip=True)

            crawl_status['step'] = f'AI 翻译：{title[:20]}…'
            summary_zh = summarize_in_chinese(title, summary_en)

            news_items.append({
                'title': title,
                'summary_zh': summary_zh,
                'summary_en': summary_en,
                'image_url': image_url,
                'link': link,
                'source': source_name,
                'published_at': published_at,
            })
        return news_items
    except Exception as e:
        print(f"Error fetching RSS {url}: {e}")
        return []


def send_email_now():
    with app.app_context():
        news_list = NewsItem.query.order_by(NewsItem.crawled_at.desc()).limit(5).all()
        subscribers = Subscriber.query.all()
        receivers = [sub.email for sub in subscribers]

    if not receivers:
        print("没有订阅者，跳过发送")
        return False
    if not news_list:
        print("没有新闻，跳过发送")
        return False

    sender_email = os.getenv('EMAIL_SENDER')
    password = os.getenv('EMAIL_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    subject = f"📰 每日国际新闻摘要 ({time.strftime('%Y-%m-%d')})"

    body = f"每日国际新闻摘要 ({time.strftime('%Y-%m-%d')})\n\n" + "=" * 50 + "\n\n"
    for i, news in enumerate(news_list, 1):
        body += f"{i}. 【{news.source}】{news.published_at}\n"
        body += f"标题: {news.title}\n"
        if news.summary_zh:
            body += f"中文摘要: {news.summary_zh}\n"
        if news.summary_en:
            body += f"原文: {news.summary_en[:200]}...\n"
        body += f"链接: {news.link}\n"
        body += "\n" + "-" * 50 + "\n\n"
    body += "---\n这是自动发送的每日新闻摘要邮件"

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ', '.join(receivers)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, password)
            server.sendmail(sender_email, receivers, msg.as_string())
        print(f"邮件发送成功，共 {len(receivers)} 个收件人")
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False


def crawl_only():
    global crawl_status
    crawl_status = {'running': True, 'step': '准备开始…', 'done': False}
    all_news = []

    for source in RSS_SOURCES:
        news = fetch_news_via_rss(source['url'], source['name'])
        all_news.extend(news)

    crawl_status['step'] = '保存到数据库…'
    with app.app_context():
        NewsItem.query.delete()
        for news in all_news:
            new_item = NewsItem(
                title=news['title'],
                summary_zh=news['summary_zh'],
                summary_en=news['summary_en'],
                image_url=news['image_url'],
                link=news['link'],
                source=news['source'],
                published_at=news['published_at'],
            )
            db.session.add(new_item)
        db.session.commit()

    crawl_status = {'running': False, 'step': '完成', 'done': True}
    print(f"爬取完成，共 {len(all_news)} 条")


def scheduler():
    schedule.every().day.at("08:00").do(crawl_only)
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Routes ──────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        email = request.form.get('email')
        if email:
            existing = Subscriber.query.filter_by(email=email).first()
            if existing:
                flash('该邮箱已订阅', 'warning')
            else:
                db.session.add(Subscriber(email=email))
                db.session.commit()
                flash('订阅成功！每天早上8点会收到新闻摘要', 'success')
            return redirect(url_for('index'))

    page = request.args.get('page', 1, type=int)
    per_page = 6
    pagination = NewsItem.query.order_by(NewsItem.crawled_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    subscribers = Subscriber.query.all()
    return render_template('index.html', news=pagination.items, pagination=pagination, subscribers=subscribers)


@app.route('/article/<int:id>')
def article(id):
    item = NewsItem.query.get_or_404(id)
    return render_template('article.html', item=item)


@app.route('/unsubscribe/<int:id>')
def unsubscribe(id):
    subscriber = Subscriber.query.get_or_404(id)
    db.session.delete(subscriber)
    db.session.commit()
    flash('已取消订阅', 'info')
    return redirect(url_for('index'))


@app.route('/crawl_now')
def crawl_now():
    if not crawl_status.get('running'):
        thread = Thread(target=crawl_only)
        thread.start()
    return redirect(url_for('index'))


@app.route('/crawl_status')
def get_crawl_status():
    return jsonify(crawl_status)


@app.route('/send_now')
def send_now():
    ok = send_email_now()
    flash('邮件发送成功！' if ok else '发送失败，请检查邮箱配置', 'success' if ok else 'warning')
    return redirect(url_for('index'))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    scheduler_thread = Thread(target=scheduler, daemon=True)
    scheduler_thread.start()

    app.run(debug=True, host='0.0.0.0', port=5000)