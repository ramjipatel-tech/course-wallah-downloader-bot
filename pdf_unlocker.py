import sys
import time
from pathlib import Path

import requests
import fitz  # PyMuPDF


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "pdf_downloads"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.goclasses.in/",
}


# ============================================================
# HELPERS
# ============================================================

def error(message):
    print(f"\n[!] ERROR: {message}")
    sys.exit(1)


def download_pdf(url, output):
    print("\n[+] Downloading PDF...")

    try:
        with requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=60,
        ) as response:

            print(
                f"[+] HTTP Status: "
                f"{response.status_code}"
            )

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            print(
                f"[+] Content-Type: "
                f"{content_type}"
            )

            with open(output, "wb") as f:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    if chunk:
                        f.write(chunk)

    except Exception as e:
        error(f"PDF download failed:\n{e}")

    if not output.exists():
        error("Downloaded PDF was not created.")

    size = output.stat().st_size

    if size == 0:
        error("Downloaded PDF is empty.")

    print(
        f"[+] Downloaded: "
        f"{size:,} bytes"
    )


def unlock_pdf(input_pdf, password, output_pdf):
    print("\n[+] Opening PDF...")

    try:
        document = fitz.open(
            str(input_pdf)
        )
    except Exception as e:
        error(f"Could not open PDF:\n{e}")

    # --------------------------------------------------------
    # Check encryption
    # --------------------------------------------------------

    if document.needs_pass:

        print(
            "[+] PDF is password protected."
        )

        if not document.authenticate(password):
            document.close()

            error(
                "Password is incorrect or "
                "PDF authentication failed."
            )

        print(
            "[+] Password accepted."
        )

    else:

        print(
            "[+] PDF is not password protected."
        )

    # --------------------------------------------------------
    # Save clean/unlocked copy
    # --------------------------------------------------------

    print(
        "[+] Creating unlocked PDF..."
    )

    try:
        document.save(
            str(output_pdf),
            garbage=4,
            deflate=True,
            clean=True,
        )

    except Exception as e:
        document.close()
        error(
            f"Could not save unlocked PDF:\n{e}"
        )

    document.close()

    if not output_pdf.exists():
        error(
            "Unlocked PDF was not created."
        )

    print(
        f"[+] Unlocked PDF created:"
    )

    print(output_pdf)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("                 PDF DOWNLOADER")
    print("              + PASSWORD REMOVER")
    print("=" * 70)

    print(
        "\nFormat:"
    )

    print(
        "PDF_URL*PASSWORD"
    )

    print()

    combined = input(
        "Input: "
    ).strip()

    # --------------------------------------------------------
    # Validate input
    # --------------------------------------------------------

    if "*" not in combined:
        error(
            "Invalid format.\n\n"
            "Use:\n"
            "PDF_URL*PASSWORD"
        )

    pdf_url, password = combined.split(
        "*",
        1,
    )

    pdf_url = pdf_url.strip()
    password = password.strip()

    if not pdf_url:
        error("PDF URL is empty.")

    if not password:
        error("PDF password is empty.")

    print(
        "\n[+] Input parsed successfully"
    )

    print(
        f"[+] PDF URL: {pdf_url}"
    )

    print(
        f"[+] Password: {'*' * len(password)}"
    )

    # --------------------------------------------------------
    # Work directory
    # --------------------------------------------------------

    timestamp = time.strftime(
        "%Y%m%d_%H%M%S"
    )

    workdir = (
        DOWNLOAD_DIR /
        f"pdf_{timestamp}"
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    encrypted_pdf = (
        workdir /
        "original.pdf"
    )

    unlocked_pdf = (
        workdir /
        "unlocked.pdf"
    )

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    download_pdf(
        pdf_url,
        encrypted_pdf,
    )

    # --------------------------------------------------------
    # Unlock
    # --------------------------------------------------------

    unlock_pdf(
        encrypted_pdf,
        password,
        unlocked_pdf,
    )

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("FINAL REPORT")
    print("=" * 70)

    print(
        "\n[+] Original:"
    )

    print(encrypted_pdf)

    print(
        "\n[+] Unlocked:"
    )

    print(unlocked_pdf)

    print(
        "\nSTATUS: SUCCESS"
    )

    print(
        "\n[+] Everything saved in:"
    )

    print(workdir)

    print(
        "\n" + "=" * 70
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()