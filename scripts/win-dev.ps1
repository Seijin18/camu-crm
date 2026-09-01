# win-dev.ps1 - sobe o camu-crm para desenvolvimento no Windows.
#
# Equivalente ao `./start.sh` (bash) para quem roda em PowerShell. Faz tudo o
# que o app precisa para funcionar, na ordem de dependencia, e e idempotente:
#
#   1. valida Docker e .env (cria a partir do .env.example se faltar)
#   2. cria/atualiza a venv em .venv e instala requirements.txt
#   3. SEMPRE puxa a imagem mais nova do Postgres (`docker compose pull`) e sobe
#      o container com `--pull always`, recriando se a imagem mudou
#   4. espera o Postgres aceitar conexao e aplica o schema (`camucrm init`)
#   5. sobe receptor e painel como processos em segundo plano
#
# Uso:  make win-dev            (ou:  powershell -ExecutionPolicy Bypass -File scripts/win-dev.ps1)
#       make win-dev SEM_PAINEL=1  -> nao sobe o painel

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$raiz = $PWD.Path
$py   = Join-Path $raiz '.venv\Scripts\python.exe'
$logs = Join-Path $raiz 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Ok($m)    { Write-Host "  [ok] $m"    -ForegroundColor Green }
function Aviso($m) { Write-Host "  [!]  $m"    -ForegroundColor Yellow }
function Erro($m)  { Write-Host "  [x]  $m"    -ForegroundColor Red }
function Etapa($m) { Write-Host "`n$m" -ForegroundColor White }

# --------------------------------------------------------------------------
Etapa 'Configuracao'

try { docker info *> $null } catch { Erro 'Docker nao esta rodando - abra o Docker Desktop e tente de novo'; exit 1 }
Ok 'Docker no ar'

if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Aviso '.env criado a partir do .env.example - preencha CAMU_TELEFONE_SALT e GEMINI_API_KEY antes de usar em serio'
} else {
  Ok '.env presente'
}

# Carrega o .env neste processo (KEY=VALUE, ignora comentarios e linhas vazias).
Get-Content '.env' | ForEach-Object {
  if ($_ -match '^\s*([^#=\s]+)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
  }
}
Ok '.env carregado'

$portaWebhook = if ($env:CAMU_WEBHOOK_PORT) { $env:CAMU_WEBHOOK_PORT } else { '8091' }
$portaPainel  = if ($env:CAMU_PAINEL_PORT)  { $env:CAMU_PAINEL_PORT }  else { '8093' }

# --------------------------------------------------------------------------
Etapa 'Ambiente Python'

if (-not (Test-Path $py)) {
  $launcher = (Get-Command py -ErrorAction SilentlyContinue)
  if ($launcher) { py -3.12 -m venv .venv } else { python -m venv .venv }
  Ok 'venv criada em .venv'
} else {
  Ok 'venv presente'
}

& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt
Ok 'dependencias instaladas (requirements.txt)'

# --------------------------------------------------------------------------
Etapa 'Banco (Docker - sempre a imagem mais nova)'

docker compose pull db
docker compose up -d --pull always db
Ok 'Postgres subindo com a imagem mais atualizada'

$pronto = $false
foreach ($i in 1..60) {
  docker exec camucrm_db pg_isready -U camu -d camucrm *> $null
  if ($LASTEXITCODE -eq 0) { $pronto = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $pronto) { Erro 'Postgres nao ficou pronto em 60s'; exit 1 }
Ok 'Postgres aceitando conexao'

& $py -m camucrm init
if ($LASTEXITCODE -ne 0) { Erro 'falha aplicando o schema'; exit 1 }
Ok 'schema aplicado'

# --------------------------------------------------------------------------
Etapa 'Servicos do CRM'

function Subir($nome, $porta, $cmd) {
  try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:$porta/health" *> $null
    Ok "$nome ja no ar (:$porta)"; return
  } catch {}
  $log = Join-Path $logs "$nome.log"
  Start-Process -FilePath $py -ArgumentList $cmd -RedirectStandardOutput $log `
    -RedirectStandardError (Join-Path $logs "$nome.err.log") -WindowStyle Hidden
  foreach ($i in 1..40) {
    try {
      Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:$porta/health" *> $null
      Ok "$nome no ar (:$porta)"; return
    } catch { Start-Sleep -Seconds 1 }
  }
  Erro "$nome nao respondeu em 40s - veja logs\$nome.log"
}

Subir 'receptor' $portaWebhook @('-m', 'camucrm', 'servir')
if (-not $env:SEM_PAINEL) {
  Subir 'painel' $portaPainel @('-m', 'camucrm', 'painel')
}

# --------------------------------------------------------------------------
Etapa 'Pronto'
Write-Host "  Painel     http://localhost:$portaPainel"
Write-Host "  Receptor   http://localhost:$portaWebhook"
Write-Host "  Fila       $py -m camucrm fila"
Write-Host "  Logs       Get-Content -Wait logs\receptor.log"
Write-Host "  Parar      docker compose stop db  +  encerre os processos python (Get-Process python)"
Write-Host ''
