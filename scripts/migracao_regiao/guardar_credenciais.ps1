# Guarda as credenciais da migração criptografadas pelo Windows (DPAPI: só o
# seu usuário, neste computador, consegue abrir). Nada aparece na tela.
# O arquivo fica fora do projeto: %LOCALAPPDATA%\agora-migracao\credenciais.json
#
# Uso (no PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File scripts\migracao_regiao\guardar_credenciais.ps1
#
# Para trocar só um item, rode de novo e deixe os outros em branco (Enter).
# No fim da migração, apague a pasta %LOCALAPPDATA%\agora-migracao.

$ErrorActionPreference = "Stop"
$pasta = Join-Path $env:LOCALAPPDATA "agora-migracao"
$arquivo = Join-Path $pasta "credenciais.json"
New-Item -ItemType Directory -Force -Path $pasta | Out-Null

$atual = @{}
if (Test-Path $arquivo) {
    (Get-Content $arquivo -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $atual[$_.Name] = $_.Value }
}

function Ler-Segredo([string]$rotulo) {
    $seguro = Read-Host -AsSecureString $rotulo
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Guardar([string]$nome, [string]$valor) {
    $seguro = ConvertTo-SecureString $valor -AsPlainText -Force
    $script:atual[$nome] = ConvertFrom-SecureString $seguro
}

$itens = @(
    @{ Nome = "ORIGEM_DB_TEMPLATE";       Rotulo = "1/9 Projeto ANTIGO (Sao Paulo) - Connect > Session pooler > string de conexao (com [YOUR-PASSWORD] mesmo)"; Comeca = "postgresql://" },
    @{ Nome = "ORIGEM_DB_PASSWORD";       Rotulo = "2/9 Projeto ANTIGO - senha do banco"; Comeca = "" },
    @{ Nome = "ORIGEM_SUPABASE_URL";      Rotulo = "3/9 Projeto ANTIGO - Project URL (https://....supabase.co)"; Comeca = "https://" },
    @{ Nome = "ORIGEM_SERVICE_ROLE_KEY";  Rotulo = "4/9 Projeto ANTIGO - chave service_role / secret"; Comeca = "" },
    @{ Nome = "DESTINO_DB_TEMPLATE";      Rotulo = "5/9 Projeto NOVO (EUA) - Connect > Session pooler > string de conexao (com [YOUR-PASSWORD] mesmo)"; Comeca = "postgresql://" },
    @{ Nome = "DESTINO_DB_PASSWORD";      Rotulo = "6/9 Projeto NOVO - senha do banco"; Comeca = "" },
    @{ Nome = "DESTINO_SUPABASE_URL";     Rotulo = "7/9 Projeto NOVO - Project URL (https://....supabase.co)"; Comeca = "https://" },
    @{ Nome = "DESTINO_SERVICE_ROLE_KEY"; Rotulo = "8/9 Projeto NOVO - chave service_role / secret"; Comeca = "" },
    @{ Nome = "SUPABASE_ACCESS_TOKEN";    Rotulo = "9/9 Token de acesso pessoal (supabase.com > Account > Access Tokens)"; Comeca = "" }
)

Write-Host ""
Write-Host "Cole cada item e aperte Enter. Nada aparece na tela. Enter vazio = manter o que ja estava guardado."
Write-Host ""

foreach ($item in $itens) {
    $valor = Ler-Segredo $item.Rotulo

    if ([string]::IsNullOrWhiteSpace($valor)) {
        if ($atual.ContainsKey($item.Nome)) { Write-Host "   mantido" } else { Write-Host "   (vazio)" }
        continue
    }

    if ($item.Comeca -and -not $valor.StartsWith($item.Comeca)) {
        Write-Host "   ATENCAO: deveria comecar com $($item.Comeca). Nao guardei; rode de novo para este item." -ForegroundColor Yellow
        continue
    }

    if ($item.Nome -like "*_DB_TEMPLATE" -and $valor -notlike "*[[]YOUR-PASSWORD[]]*") {
        Write-Host "   ATENCAO: cole a string com [YOUR-PASSWORD] (a senha vai no item seguinte). Nao guardei." -ForegroundColor Yellow
        continue
    }

    Guardar $item.Nome $valor
    Write-Host "   guardado ($($valor.Length) caracteres)"
}

$atual | ConvertTo-Json | Set-Content -Path $arquivo -Encoding UTF8
Write-Host ""
Write-Host "Pronto. Itens guardados: $($atual.Keys.Count) de $($itens.Count)."
