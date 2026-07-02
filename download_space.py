from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="sureshhari/snchatbot-backend",
    repo_type="space",
    local_dir=".",
    local_dir_use_symlinks=False,
)