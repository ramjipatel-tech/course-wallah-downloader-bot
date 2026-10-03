from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return """
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SudoR2spr Repository</title>
    <link rel="icon" type="image/x-icon" href="https://tinypic.host/images/2025/02/07/DeWatermark.ai_1738952933236-1.png">
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #0f0f0f, #1a1a1a);
            color: #fff;
            font-family: 'Courier New', monospace;
            text-align: center;
            padding-top: 50px;
        }
        .ascii-art {
            font-size: 14px;
            color: #0ff;
            transition: transform 0.3s ease;
        }
        .ascii-art:hover {
            transform: scale(1.1);
            color: #ff0066;
        }
        .thanos {
            font-size: 40px;
            font-weight: bold;
            color: #ffcc00;
            display: inline-block;
            cursor: pointer;
        }
        footer {
            margin-top: 80px;
            padding: 20px;
            background: #111;
        }
        footer img {
            border-radius: 50%;
        }
        /* Thanos effect keyframes */
        @keyframes vanish {
            0% { opacity: 1; transform: translate(0,0) rotate(0); }
            50% { opacity: 0.5; transform: translate(20px,-20px) rotate(20deg); }
            100% { opacity: 0; transform: translate(-50px,50px) rotate(-90deg); }
        }
        .disintegrate span {
            display: inline-block;
            animation: vanish 1s forwards;
        }
    </style>
</head>

<body>
    <div class="container">
        <a href="https://github.com/DevThanos" class="text-decoration-none text-light">
            <pre class="ascii-art">
            
████████╗██╗  ██╗ █████╗ ███╗   ██╗ ██████╗ ███████╗
╚══██╔══╝██║  ██║██╔══██╗████╗  ██║██╔═══██╗██╔════╝
   ██║   ███████║███████║██╔██╗ ██║██║   ██║███████╗
   ██║   ██╔══██║██╔══██║██║╚██╗██║██║   ██║╚════██║
   ██║   ██║  ██║██║  ██║██║ ╚████║╚██████╔╝███████║
   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝
            </pre>
        </a>

        <h1 id="saini" class="thanos">Thanos™</h1>
        <p><b>v2.0.0</b></p>
    </div>

    <footer>
        <center>
            <img src="https://files.catbox.moe/ui41xs.jpg" width="40" height="40">
            Powered By Thanos 
            <img src="https://files.catbox.moe/ui41xs.jpg" width="40" height="40">
            <div class="footer__copyright">
                <p>© 2025 TXT Links Downloader.</p>
            </div>
        </center>
    </footer>

    <script>
        // Thanos Disintegration effect
        const text = document.getElementById("saini");
        text.addEventListener("click", () => {
            const letters = text.innerText.split("");
            text.innerHTML = "";
            letters.forEach((letter, i) => {
                const span = document.createElement("span");
                span.innerText = letter;
                span.style.animationDelay = (i * 0.1) + "s";
                span.classList.add("disintegrate");
                text.appendChild(span);
            });
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)
