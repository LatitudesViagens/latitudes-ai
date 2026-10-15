# Roda UM comando da migração com as credenciais do cofre (guardar_credenciais.ps1)
# carregadas só para ele, e TROCA qualquer senha ou chave por *** na saída,
# mesmo que alguma ferramenta tente imprimi-las.
#
# Variáveis disponíveis para o comando:
#   $env:ORIGEM_DB_URL, $env:DESTINO_DB_URL (string de conexão já com a senha)
#   $env:ORIGEM_SUPABASE_URL, $env:ORIGEM_SERVICE_ROLE_KEY
#   $env:DESTINO_SUPABASE_URL, $env:DESTINO_SERVICE_ROLE_KEY
#   $env:SUPABASE_ACCESS_TOKEN, $env:ORIGEM_REF, $env:DESTINO_REF
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts\migracao_regiao\com_credenciais.ps1 -Comando 'python scripts/migracao_regiao/copiar_arquivos.py --contar'

param([Parameter(Mandatory = $true)][string]$Comando)

$ErrorActionPreference = "Stop"
$arquivo = Join-Path $env:LOCALAPPDATA "agora-migracao\credenciais.json"

if (-not (Test-Path $arquivo)) {
    Write-Host "Cofre nao encontrado. Rode antes: scripts\migracao_regiao\guardar_credenciais.ps1"
    exit 2
}

$segredos = New-Object System.Collections.Generic.List[string]
$valores = @{}

(Get-Content $arquivo -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object {
    $seguro = $_.Value | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
    try { $texto = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    $valores[$_.Name] = $texto
}

foreach ($lado in @("ORIGEM", "DESTINO")) {
    $modelo = $valores["$($lado)_DB_TEMPLATE"]
    $senha = $valores["$($lado)_DB_PASSWORD"]

    if ($modelo -and $senha) {
        # A senha entra codificada na URL (caracteres como @ : / quebrariam a conexão).
        $url = $modelo.Replace("[YOUR-PASSWORD]", [Uri]::EscapeDataString($senha))
        $valores["$($lado)_DB_URL"] = $url
        $segredos.Add($url)
        $segredos.Add([Uri]::EscapeDataString($senha))
    }

    $projeto = $valores["$($lado)_SUPABASE_URL"]
    if ($projeto -match "https://([a-z0-9]+)\.supabase\.co") {
        $valores["$($lado)_REF"] = $Matches[1]
        # Só o endereço base (sem /rest/v1/ ou barra no fim, se vierem junto).
        $valores["$($lado)_SUPABASE_URL"] = "https://$($Matches[1]).supabase.co"
    }
}

foreach ($nome in @("ORIGEM_DB_PASSWORD", "DESTINO_DB_PASSWORD", "ORIGEM_SERVICE_ROLE_KEY", "DESTINO_SERVICE_ROLE_KEY", "SUPABASE_ACCESS_TOKEN")) {
    if ($valores[$nome] -and $valores[$nome].Length -ge 6) { $segredos.Add($valores[$nome]) }
}

foreach ($nome in $valores.Keys) {
    if ($nome -notlike "*_TEMPLATE") { [Environment]::SetEnvironmentVariable($nome, $valores[$nome], "Process") }
}

$ErrorActionPreference = "Continue"
# Os mais longos primeiro, para não sobrar pedaço de segredo na saída.
$ordenados = $segredos | Sort-Object Length -Descending

Invoke-Expression "& { $Comando } 2>&1" | ForEach-Object {
    $linha = "$_"
    foreach ($s in $ordenados) { $linha = $linha.Replace($s, "***") }
    $linha
}

exit $LASTEXITCODE
