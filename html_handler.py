import os
import requests
import subprocess
from vars import CREDIT
from pyrogram import Client, filters
from pyrogram.types import Message

#==================================================================================================================================

# Function to extract names and URLs from the text file
def extract_names_and_urls(file_content):
    lines = file_content.strip().split("\n")
    data = []
    for line in lines:
        if ":" in line:
            name, url = line.split(":", 1)
            data.append((name.strip(), url.strip()))
    return data

#==================================================================================================================================

# Function to categorize URLs
def categorize_urls(urls):
    videos = []
    pdfs = []
    others = []

    for name, url in urls:
        new_url = url
        if "akamaized.net/" in url or "1942403233.rsc.cdn77.org/" in url:
            new_url = f"https://www.khanglobalstudies.com/player?src={url}"
            videos.append((name, new_url))

        elif "d1d34p8vz63oiq.cloudfront.net/" in url:
            pw_token = os.environ.get("PW_TOKEN", "")
            new_url = f"https://anonymouspwplayer-0e5a3f512dec.herokuapp.com/pw?url={url}&token={pw_token}"
            videos.append((name, new_url))
                    
        elif "youtube.com/embed" in url:
            yt_id = url.split("/")[-1]
            new_url = f"https://www.youtube.com/watch?v={yt_id}"
            videos.append((name, new_url))

        elif ".m3u8" in url or ".mp4" in url:
            videos.append((name, url))
        elif "pdf" in url:
            pdfs.append((name, url))
        else:
            others.append((name, url))

    return videos, pdfs, others

#=================================================================================================================================

# Function to generate HTML file
def generate_html(file_name, videos, pdfs, others):
    file_name_without_extension = os.path.splitext(file_name)[0]

    video_links = "".join(f'<a href="#" onclick="playVideo(\'{url}\')">🎬 {name}</a>' for name, url in videos)
    pdf_links = "".join(f'<a href="{url}" target="_blank">📄 {name}</a>' for name, url in pdfs)
    other_links = "".join(f'<a href="{url}" target="_blank">🌐 {name}</a>' for name, url in others)

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{file_name_without_extension}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <link href="https://vjs.zencdn.net/8.10.0/video-js.css" rel="stylesheet" />
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Arial', sans-serif;
            font-size: 14px;
        }}
        body {{
            background: #f9f9f9;
            color: #333;
            line-height: 1.4;
        }}
        header {{
            background: #1c1c1c;
            color: white;
            padding: 10px;
            text-align: center;
            font-size: 16px;
            font-weight: bold;
        }}
        header p {{
            font-size: 12px;
            color: #bbb;
            margin-top: 3px;
        }}
        #video-player {{
            margin: 15px auto;
            width: 95%;
            max-width: 700px;
        }}
        .search-bar {{
            margin: 15px auto;
            width: 95%;
            max-width: 500px;
            text-align: center;
        }}
        .search-bar input {{
            width: 100%;
            padding: 8px;
            border: 1px solid #007bff;
            border-radius: 5px;
            font-size: 14px;
        }}
        .container {{
            display: flex;
            justify-content: space-around;
            margin: 15px auto;
            width: 95%;
            max-width: 700px;
        }}
        .tab {{
            flex: 1;
            padding: 10px;
            background: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            cursor: pointer;
            border-radius: 5px;
            font-size: 14px;
            margin: 0 3px;
            text-align: center;
        }}
        .tab:hover {{
            background: #007bff;
            color: white;
        }}
        .content {{
            display: none;
            margin: 15px auto;
            width: 95%;
            max-width: 700px;
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .content h2 {{
            font-size: 16px;
            margin-bottom: 10px;
            color: #007bff;
        }}
        .video-list a, .pdf-list a, .other-list a {{
            display: block;
            padding: 8px;
            margin: 3px 0;
            background: #f2f2f2;
            border-radius: 5px;
            text-decoration: none;
            color: #007bff;
            font-size: 13px;
        }}
        .video-list a:hover, .pdf-list a:hover, .other-list a:hover {{
            background: #007bff;
            color: white;
        }}
        footer {{
            margin-top: 20px;
            font-size: 12px;
            padding: 10px;
            background: #1c1c1c;
            color: #ddd;
            text-align: center;
        }}
    </style>
</head>
<body>
    <header>
        📚 {file_name_without_extension}
        <p>|| {len(videos)} Videos • {len(pdfs)} PDFs • {len(others)} Others ||</p>
    </header>

    <div id="video-player">
        <video id="engineer-babu-player" class="video-js vjs-default-skin" controls preload="auto" width="640" height="360"></video>
    </div>

    <div class="search-bar">
        <input type="text" id="searchInput" placeholder="Search for videos, PDFs, or other resources..." oninput="filterContent()">
    </div>

    <div class="container">
        <div class="tab" onclick="showContent('videos')">🎬 Videos</div>
        <div class="tab" onclick="showContent('pdfs')">📄 PDFs</div>
        <div class="tab" onclick="showContent('others')">🌐 Others</div>
    </div>

    <div id="videos" class="content">
        <h2>🎬 Video Lectures</h2>
        <div class="video-list">{video_links}</div>
    </div>

    <div id="pdfs" class="content">
        <h2>📄 PDFs</h2>
        <div class="pdf-list">{pdf_links}</div>
    </div>

    <div id="others" class="content">
        <h2>📦 Other Resources</h2>
        <div class="other-list">{other_links}</div>
    </div>

    <footer>Extracted By ⌯ FʀᴏɴᴛMᴀɴ | ×͜× |</footer>

    <script src="https://vjs.zencdn.net/8.10.0/video.min.js"></script>
    <script>
        const player = videojs('engineer-babu-player', {{
            controls: true, autoplay: false, preload: 'auto', fluid: true
        }});
        function playVideo(url) {{
            if (url.includes('.m3u8')) {{
                player.src({{ src: url, type: 'application/x-mpegURL' }});
                player.play().catch(() => window.open(url, '_blank'));
            }} else {{
                window.open(url, '_blank');
            }}
        }}
        function showContent(tabName) {{
            document.querySelectorAll('.content').forEach(c => c.style.display = 'none');
            document.getElementById(tabName).style.display = 'block';
            filterContent();
        }}
        function filterContent() {{
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const categories = ['videos','pdfs','others'];
            categories.forEach(cat => {{
                const items = document.querySelectorAll(`#${{cat}} .${{cat}}-list a`);
                let catResult = false;
                items.forEach(item => {{
                    if (item.textContent.toLowerCase().includes(searchTerm)) {{
                        item.style.display = 'block'; catResult = true;
                    }} else item.style.display = 'none';
                }});
                document.querySelector(`#${{cat}} h2`).style.display = catResult ? 'block' : 'none';
            }});
        }}
        document.addEventListener('DOMContentLoaded', () => showContent('videos'));
    </script>
</body>
</html>
    """
    return html_template

#==================================================================================================================================

# Optional: Function to download video with FFmpeg
def download_video(url, output_path):
    cmd = ["ffmpeg", "-y", "-i", str(url), "-c", "copy", str(output_path)]
    subprocess.run(cmd, check=True)

#==================================================================================================================================

async def html_handler(bot: Client, message: Message):
    editable = await message.reply_text("𝐖𝐞𝐥𝐜𝐨𝐦𝐞! 𝐏𝐥𝐞𝐚𝐬𝐞 𝐮𝐩𝐥𝐨𝐚𝐝 𝐚 .𝐭𝐱𝐭 𝐟𝐢𝐥𝐞 𝐜𝐨𝐧𝐭𝐚𝐢𝐧𝐢𝐧𝐠 𝐔𝐑𝐋𝐬.✓")
    user_id = message.from_user.id if message.from_user else None
    input: Message = await bot.listen(chat_id=editable.chat.id, user_id=user_id, timeout=120)
    if input.document and input.document.file_name.endswith('.txt'):
        file_path = await input.download()
        file_name, ext = os.path.splitext(os.path.basename(file_path))
        b_name = file_name.replace('_', ' ')
    else:
        await message.reply_text("**• Invalid file input.**")
        return
           
    with open(file_path, "r") as f:
        file_content = f.read()

    urls = extract_names_and_urls(file_content)
    videos, pdfs, others = categorize_urls(urls)

    html_content = generate_html(file_name, videos, pdfs, others)
    html_file_path = file_path.replace(".txt", ".html")
    with open(html_file_path, "w") as f:
        f.write(html_content)

    await message.reply_document(
        document=html_file_path, 
        caption=f"🌐 𝐇𝐓𝐌𝐋 𝐅𝐢𝐥𝐞 𝐂𝐫𝐞𝐚𝐭𝐞𝐝!\n<blockquote><b>`{b_name}`</b></blockquote>\n🌟 Extracted By : {CREDIT}"
    )
    os.remove(file_path)
    os.remove(html_file_path)
