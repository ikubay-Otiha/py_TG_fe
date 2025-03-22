import os
import json
import tempfile
import logging

from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
# アップロードファイルは25MBまで
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# アップロードフォルダが存在しない場合は作成
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# BACKEND_URL = "http://nginx_proxy/cpp/transcription"
BACKEND_URL = os.environ.get("BACKEND_URL", "http://nginx_proxy/transcription")
# BACKEND_URL="localhost/cpp/transcription"

# 許可される拡張子リスト
ALLOWED_EXTENSIONS = [
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".wav",
    ".webm",
]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
handler.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

logger.addHandler(handler)


@app.route("/")
def index():
    logger.info("accessed root")
    return render_template("index.html")


@app.route("/transcription", methods=["POST"])
def transcribe_audio():
    # validate form data
    if "audio" not in request.files:
        logger.error(f"error: 音声ファイルが見つかりません {request.files}")
        return jsonify({"error": "音声ファイルが見つかりません"}), 400

    if "title" not in request.form:
        logger.error(f"error: タイトルが入力されていません 400 {request.form}")
        return jsonify({"error": "タイトルが入力されていません"}), 400

    if "datetime" not in request.form:
        logger.error(f"error: 日時が入力されていません 400 {request.form}")
        return jsonify({"error": "日時が入力されていません"}), 400

    audio_file = request.files.get("audio")
    title = request.form.get("title")
    datetime = request.form.get("datetime")

    if audio_file.filename == "":
        logger.error(f"error: 選択されたファイルがありません {audio_file.filename}")
        return jsonify({"error": "選択されたファイルがありません"}), 400

    # ファイル拡張子の確認
    filename = audio_file.filename
    file_ext = os.path.splitext(filename)[1].lower()

    if file_ext not in ALLOWED_EXTENSIONS:
        logger.error(f"error: 対応していないファイル形式です {file_ext}")
        return jsonify(
            {
                "error": f"対応していないファイル形式です。対応形式: {', '.join(ALLOWED_EXTENSIONS)}"
            }
        ), 400

    logger.info(
        f"File: {filename}, Type: {audio_file.content_type}, Size: {os.fstat(audio_file.fileno()).st_size} bytes"
    )

    try:
        # ファイルの基本情報をログに記録
        file_size = 0
        file_content = audio_file.read()
        file_size = len(file_content)
        audio_file.seek(0)  # ファイルポインタを先頭に戻す
        logger.info(
            f"File: {filename}, Type: {audio_file.content_type}, Size: {file_size} bytes"
        )

        # 一時ファイルにデータを保存してバイナリ内容を確認（デバッグ用）
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(file_content)

        # print(f"Saved to temporary file: {temp_file_path}")

        # # ファイルのヘッダーバイトを出力（デバッグ用）
        # with open(temp_file_path, "rb") as f:
        #     header_bytes = f.read(16)
        #     header_hex = " ".join([f"{b:02x}" for b in header_bytes])
        #     print(f"File header bytes: {header_hex}")

        try:
            # クライアントのIPアドレスを取得
            client_ip = request.remote_addr
            logger.info(f"Client IP address: {client_ip}")

            # リクエストヘッダーを設定（X-Forwarded-Forを含む）
            headers = {
                "X-Forwarded-For": client_ip,
                # バックエンドがX-Real-IPも見ている可能性があるので追加
                "X-Real-IP": client_ip,
            }

            # 一時ファイルを使って送信
            with open(temp_file_path, "rb") as f:
                files = {"audio": (filename, f, audio_file.content_type)}
                data = {"title": title, "datetime": datetime}

                logger.info(f"Sending request to {BACKEND_URL}")
                response = requests.post(
                    url=BACKEND_URL, files=files, data=data, headers=headers
                )

            # デバッグ用にレスポンスの詳細をログに出力
            logger.debug(f"Backend response status: {response.status_code}")
            logger.debug(f"Backend response headers: {response.headers}")
            logger.debug(f"Backend response content: {response.text[:200]}...")

            # JSONレスポンスを解析
            try:
                response_json = response.json()

                if "transcription" in response_json:
                    transcription = response_json.get("transcription")

                    # whisperAPIからのエラーメッセージが含まれているか確認
                    if (
                        isinstance(transcription, str)
                        and transcription.startswith("{")
                        and "error" in transcription.lower()
                    ):
                        logger.error("error message detection")
                        try:
                            # JSON文字列をパースしてエラーメッセージを抽出
                            error_obj = json.loads(transcription)
                            if "error" in error_obj and "message" in error_obj["error"]:
                                error_msg = error_obj["error"]["message"]
                                logger.error(f"音声認識エラー {error_msg}")
                                return jsonify(
                                    {"error": f"音声認識エラー: {error_msg}"}
                                )
                        except json.JSONDecodeError:
                            logger.error(f"JSONデコードエラー {json}")
                            # JSONとして解析できない場合は通常の応答を返す
                            pass

                # 正常なレスポンスを返す
                logger.info("json parse completed")
                return jsonify(response_json)

            except json.JSONDecodeError:
                logger.error(
                    f"バックエンドからの応答がJSONではありません {response.text}"
                )
                return jsonify(
                    {
                        "error": "バックエンドからの応答がJSONではありません",
                        "message": response.text[:200],
                    }
                ), 500

        except Exception as e:
            logger.error(f"バックエンドへの接続エラー {e}")
            return jsonify({"error": f"バックエンドへの接続エラー: {str(e)}"})

    except Exception as e:
        import traceback

        logger.error(f"Exception occurred: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"処理中にエラーが発生しました: {str(e)}"})

    finally:
        # 一時ファイルの削除
        if "temp_file_path" in locals() and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
            logger.info(f"Deleted temporary file: {temp_file_path}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=3020)
    logger.info("Server started")
