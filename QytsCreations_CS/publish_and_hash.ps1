# Builds the C# exe, computes SHA256, writes it into version.json,
# stages everything and creates/updates the GitHub release with the new asset.
# Run from anywhere — paths are absolute.

param(
    [string]$Version = $null   # optional override; otherwise read from csproj
)

$ErrorActionPreference = 'Stop'
$root  = "C:\Users\prkid\Documents\macro_recorder\QytsCreations_CS"
$exe   = "$root\publish\QytCroRec.exe"
$asset = "$root\publish\QytCroRec-CS.exe"
$vfile = "$root\version.json"

# ── 1. Resolve version
if (-not $Version)
{
    $csproj = Get-Content "$root\QytCroRec.csproj" -Raw
    if ($csproj -match '<Version>([^<]+)</Version>') { $Version = $Matches[1] }
    else { throw "Could not read <Version> from csproj." }
}
"Version: $Version"

# ── 2. Build single-file exe
Push-Location $root
Remove-Item -Recurse -Force "$root\publish" -ErrorAction SilentlyContinue
dotnet publish -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:PublishReadyToRun=true `
    -p:EnableCompressionInSingleFile=true -o "$root\publish"
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "dotnet publish failed." }
Pop-Location

# ── 3. Hash + rename to release asset name
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"SHA256: $sha"
Copy-Item $exe $asset -Force

# ── 4. Read+merge existing version.json, write back with new sha + version
$obj = @{}
if (Test-Path $vfile)
{
    try { $obj = Get-Content $vfile -Raw | ConvertFrom-Json -AsHashtable } catch { $obj = @{} }
}
$obj["version"] = $Version
$obj["sha256"]  = $sha
if (-not $obj.ContainsKey("changelog") -or -not $obj["changelog"])
{
    $obj["changelog"] = "Release v$Version"
}
$json = $obj | ConvertTo-Json -Depth 5
Set-Content -Path $vfile -Value $json -Encoding utf8
"version.json updated:"
Get-Content $vfile

"OK. Next:"
"  git add QytsCreations_CS/version.json && git commit && git push"
"  gh release upload v$Version-cs `"$asset`" --clobber"
