# Install PyTorch into the Research OS venv using the official wheel index (https://pytorch.org/get-started/locally/).
# RTX 50-series (Blackwell) needs a CUDA 12.8+ build. Check the selector page for the current command first.
param([ValidateSet('cpu','cu128','cu129','cu130')][string]$Variant = 'cu128')
. "$PSScriptRoot\_common.ps1"
Assert-Venv
Write-Host "Installing torch ($Variant) from https://download.pytorch.org/whl/$Variant"
uv pip install --python $Py torch --index-url "https://download.pytorch.org/whl/$Variant"
& $Py -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'available', torch.cuda.is_available())"
Write-Host "Next: run the gpu_preflight template (doctor shows its result). is_available alone is NOT a GPU pass."
