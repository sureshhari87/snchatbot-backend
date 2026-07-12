import os

from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="sureshhari/snchatbot-backend",
    repo_type="space",
    revision=os.getenv("HF_SPACE_REVISION", "main"),
    local_dir=".",
    local_dir_use_symlinks=False,
)
