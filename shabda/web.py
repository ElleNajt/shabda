"""Shabda web routes"""

import asyncio
import json
import os
import tempfile
from urllib.parse import urlparse
from zipfile import ZipFile

from flask import (
    Blueprint,
    after_this_request,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.exceptions import BadRequest, HTTPException

from shabda.dj import Dj

SHABDA_PATH = os.path.expanduser("~/.shabda/")

SAMPLES_PATH = "samples/"
SPEECH_SAMPLE_PATH = "speech_samples/"

bp = Blueprint("web", __name__, url_prefix="/")

dj = Dj(SHABDA_PATH, SAMPLES_PATH, SPEECH_SAMPLE_PATH)


@bp.route("/")
def home():
    """Main page"""
    return render_template("home.html")


@bp.route("/pack/<definition>")
async def pack(definition):
    """Retrieve a pack of samples"""
    licenses = request.args.get("licenses")
    if licenses is not None:
        licenses = licenses.split(",")

    tasks = []
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex

    for word, number in words.items():
        if number is None:
            number = 1
        tasks.append(fetch_one(word, number, licenses))
    results = await asyncio.gather(*tasks)

    global_status = "empty"
    for status in results:
        if status is True:
            global_status = "ok"

    return jsonify(
        {
            "status": global_status,
            "definition": clean_definition(words),
        }
    )


@bp.route("/<definition>.json")
async def pack_json(definition):
    """Download a reslist definition"""
    complete = request.args.get("complete", False, type=bool)
    licenses = request.args.get("licenses", None)
    strudel = request.args.get("strudel", False, type=bool)
    if licenses is not None:
        licenses = licenses.split(",")

    await pack(definition)

    url = urlparse(request.base_url)
    # SORRY :( QUICK HACK TO SUPPORT MY REVERSE PROXY
    # base = url.scheme + "://" + url.hostname
    base = "https://" + url.hostname
    if url.port:
        base += ":" + str(url.port)
    script_name = request.environ.get("SCRIPT_NAME", "")
    base += script_name + "/"
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex
    if strudel:
        reslist = {"_base": base}
    else:
        reslist = []
    for word, number in words.items():
        samples = dj.list(word, number, licenses=licenses)
        sample_num = 0
        for sound in samples:
            if strudel:
                if word not in reslist:
                    reslist[word] = []
                reslist[word].append(sound.file)
            else:
                sound_data = {
                    "url": sound.file,
                    "type": "audio",
                    "bank": word,
                    "n": sample_num,
                }
                if complete:
                    sound_data["licensename"] = sound.licensename
                    sound_data["original_url"] = sound.url
                    sound_data["author"] = sound.username
                reslist.append(sound_data)
                sample_num += 1

    return jsonify(reslist)


@bp.route("/<definition>.zip")
def pack_zip(definition):
    """Download a zip archive"""
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex
    definition = clean_definition(words)
    tmpfile = tempfile.gettempdir() + "/" + definition + ".zip"
    with ZipFile(tmpfile, "w") as zipfile:
        for word, number in words.items():
            samples = dj.list(word, number)
            for sample in samples:
                zipfile.write(sample.file, sample.file[len("samples/") :])

    @after_this_request
    def remove_file(response):
        os.remove(tmpfile)
        return response

    return send_file(tmpfile, as_attachment=True)


@bp.route("/speech/<definition>.zip")
def speech_zip(definition):
    """Download a zip archive"""
    definition = definition.replace(" ", "_")
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex
    tmpfile = tempfile.gettempdir() + "/" + definition + ".zip"
    with ZipFile(tmpfile, "w") as zipfile:
        for word, number in words.items():
            samples = dj.list(word, number, soundtype="tts")
            for sample in samples:
                zipfile.write(sample.file, sample.file[len("speech_samples/") :])

    @after_this_request
    def remove_file(response):
        os.remove(tmpfile)
        return response

    return send_file(tmpfile, as_attachment=True)


@bp.route("/speech/<definition>")
async def speech(definition):
    """Download a spoken word"""
    gender = request.args.get("gender", "f")
    language = request.args.get("language", "en-GB")
    pitch = request.args.get("pitch", 0.0, type=float)

    definition = definition.replace(" ", "_")
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex
    tasks = []
    for word in words:
        tasks.append(speak_one(word, language, gender, pitch))
    results = await asyncio.gather(*tasks)
    global_status = "empty"
    for status in results:
        if status is True:
            global_status = "ok"

    return jsonify(
        {
            "status": global_status,
            "definition": clean_definition(words),
        }
    )


@bp.route("/speech/<definition>.json")
async def speech_json(definition):
    """Download a reslist definition"""
    strudel = request.args.get("strudel", False, type=bool)
    gender = request.args.get("gender", "f")
    language = request.args.get("language", "en-GB")
    pitch = request.args.get("pitch", 0.0, type=float)
    definition = definition.replace(" ", "_")

    await speech(definition)

    url = urlparse(request.base_url)
    # base = url.scheme + "://" + url.hostname
    base = "https://" + url.hostname
    if url.port:
        base += ":" + str(url.port)
    # Include SCRIPT_NAME prefix (e.g. /shabda when mounted under FastAPI)
    script_name = request.environ.get("SCRIPT_NAME", "")
    base += script_name + "/"
    try:
        words = dj.parse_definition(definition)
    except ValueError as ex:
        raise BadRequest(ex) from ex
    if strudel:
        reslist = {"_base": base}
    else:
        reslist = []
    for word in words:
        samples = dj.list(word, gender=gender, language=language, soundtype="tts")
        # Filter to only the sample matching the requested pitch
        if pitch != 0.0:
            pitch_suffix = f"_p{pitch:+.1f}"
            samples = [s for s in samples if pitch_suffix in s.file]
            pitch_int = int(pitch) if pitch == int(pitch) else pitch
            sample_key = f"{word}_p{pitch_int}"
        else:
            # When no pitch, exclude any pitched variants
            samples = [
                s for s in samples if "_p+" not in s.file and "_p-" not in s.file
            ]
            sample_key = word
        sample_num = 0
        for sound in samples:
            if strudel:
                if sample_key not in reslist:
                    reslist[sample_key] = []
                reslist[sample_key].append(sound.file)
            else:
                sound_data = {
                    "url": sound.file,
                    "type": "audio",
                    "bank": sample_key,
                    "n": sample_num,
                }
                reslist.append(sound_data)
                sample_num += 1

    return jsonify(reslist)


@bp.route("/speech_samples/<path:path>")
@bp.route("/speech/speech_samples/<path:path>")
def serve_sample(path):
    """Serve a sample"""
    return send_from_directory(
        os.path.abspath(SPEECH_SAMPLE_PATH), path, as_attachment=False
    )


@bp.route("/samples/<path:path>")
def serve_speech_sample(path):
    """Serve a sample"""
    return send_from_directory(os.path.abspath(SAMPLES_PATH), path, as_attachment=False)


@bp.route("/assets/<path:path>")
def static(path):
    """Serve a static asset"""
    return send_from_directory("../assets/", path, as_attachment=False)


@bp.errorhandler(HTTPException)
def handle_exception(exception):
    """Return JSON instead of HTML for HTTP errors."""
    # start with the correct headers and status code from the error
    response = exception.get_response()
    # replace the body with JSON
    response.data = json.dumps(
        {
            "code": exception.code,
            "name": exception.name,
            "description": exception.description,
        }
    )
    response.content_type = "application/json"
    return response


@bp.after_request
def cors_after(response):
    """Add CORS headers to response"""
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


async def speak_one(word, language, gender, pitch=0.0):
    """Speak a word"""
    return await dj.speak(word, language, gender, pitch)


async def fetch_one(word, number, licenses):
    """Fetch a single sample set"""
    return await dj.fetch(word, number, licenses)


def clean_definition(words):
    """reconstruct the definition without unwanted chars"""
    definition = []
    for word, number in words.items():
        definition.append(word + (":" + str(number) if number else ""))
    return ",".join(definition)
