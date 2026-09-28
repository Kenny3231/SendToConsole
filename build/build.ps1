# build.ps1 - Construit SendToConsole
#
#   cd D:\Projets\SendToConsole
#   .\build\build.ps1              # dossier (onedir) - recommande, demarrage instantane
#   .\build\build.ps1 -OneFile     # fichier unique .exe - plus lent a chaque lancement
#   .\build\build.ps1 -FullQt      # sans elagage des DLL Qt (diagnostic)
#   .\build\build.ps1 -KeepVault   # usage personnel : garde ton entries.enc dans dist
#
# Coffre (entries.enc) : par defaut, dist\ produit est LIVRABLE, donc sans
# coffre. Si un entries.enc existait deja dans dist\ (cree en lancant l'exe
# de dist), il est mis a l'abri dans .\.vault-stash\ (jamais supprime) et
# n'est PAS remis dans dist\. Avec -KeepVault, il est restitue dans dist\
# comme avant : NE PAS distribuer ce dossier dans ce cas.
#
# En cas de refus d'execution de script par Windows :
#   powershell -ExecutionPolicy Bypass -File .\build\build.ps1

[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$FullQt,
    [switch]$KeepVault
)

$ErrorActionPreference = "Stop"

# Le Python a utiliser. Sur ce poste, "python" seul pointe sur
# l'interpreteur d'Inkscape : on passe donc par le lanceur avec la version.
$Py = "py"
$PyArgs = @("-V:3.12")

# Restreint un fichier/dossier du coffre a l'utilisateur courant + SYSTEM +
# Administrateurs, sans heritage (Copy-Item ne recopie pas la DACL : sans cela
# les copies herite(raie)nt des droits du projet, souvent « Utilisateurs »).
function Protect-VaultPath([string]$Target, [switch]$Directory) {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $inh = if ($Directory) { "(OI)(CI)" } else { "" }
    & icacls $Target /inheritance:r /grant:r "*${sid}:${inh}(F)" `
        "*S-1-5-18:${inh}(F)" "*S-1-5-32-544:${inh}(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Avertissement : droits non restreints sur $Target (icacls code $LASTEXITCODE)." -ForegroundColor DarkYellow
    }
}

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    # Une instance lancee depuis dist\ ou build\ verrouille ses fichiers
    # (effacement partiel, puis echec de PyInstaller) et pourrait reecrire un
    # coffre dans le dossier livrable. Chemin illisible (processus eleve) :
    # on ne peut pas trancher, donc on bloque aussi.
    $root = (Get-Location).Path
    $running = Get-Process -Name SendToConsole -ErrorAction SilentlyContinue |
        Where-Object { -not $_.Path -or $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        Write-Host "Echec : SendToConsole tourne encore (PID $($running.Id -join ', ')) et verrouille ses fichiers." -ForegroundColor Red
        Write-Host "  Quittez-le (tray > Quitter, ou OK sur une boite d'erreur) puis relancez le build." -ForegroundColor Red
        exit 1
    }

    if ($OneFile) { $env:STC_ONEFILE = "1" }  else { $env:STC_ONEFILE = "0" }
    if ($FullQt)  { $env:STC_NO_PRUNE = "1" } else { $env:STC_NO_PRUNE = "0" }

    if ($OneFile) { $mode = "fichier unique (onefile)" } else { $mode = "dossier (onedir)" }
    Write-Host "== Mode : $mode ==" -ForegroundColor Cyan

    Write-Host "`n== Dependances ==" -ForegroundColor Cyan
    & $Py @PyArgs -m pip install --upgrade pip setuptools wheel
    # Versions epinglees, identiques a celles du workflow de release.
    & $Py @PyArgs -m pip install -r requirements-build.txt

    Write-Host "`n== Icone ==" -ForegroundColor Cyan
    & $Py @PyArgs build\make_icon.py

    # --- Protection du fichier d'entrees chiffrees ---------------------------
    # Il est ecrit A COTE de l'executable : en onedir il vit DANS dist\SendToConsole\,
    # que l'etape de nettoyage ci-dessous efface. On le met TOUJOURS de cote avant
    # le nettoyage (dans .vault-stash\, hors dist\ et hors build\SendToConsole\),
    # et on verifie la copie avant d'effacer quoi que ce soit.
    # TOUS les coffres presents sont mis de cote (onedir ET onefile peuvent
    # coexister si on alterne les modes) : dist\ est ensuite efface en entier.
    $stashes = @()
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($candidate in @(
            @{ Path = "dist\SendToConsole\entries.enc"; Tag = "onedir";  OneFile = $false },
            @{ Path = "dist\entries.enc";               Tag = "onefile"; OneFile = $true })) {
        if (Test-Path $candidate.Path) {
            $stashDir = Join-Path (Get-Location) ".vault-stash"
            if (-not (Test-Path $stashDir)) {
                New-Item -ItemType Directory -Force $stashDir | Out-Null
            }
            # Dossier restreint AVANT la copie : le fichier copie herite
            # uniquement de ces droits-la.
            Protect-VaultPath $stashDir -Directory
            $stash = Join-Path $stashDir ("entries-{0}-{1}.enc" -f $stamp, $candidate.Tag)
            Copy-Item $candidate.Path $stash -Force
            $hash = (Get-FileHash $stash).Hash
            if ((Get-FileHash $candidate.Path).Hash -ne $hash) {
                Write-Host "Echec : copie de securite du coffre non conforme, build annule (rien n'a ete efface)." -ForegroundColor Red
                exit 1
            }
            Write-Host "`nEntrees chiffrees mises a l'abri : $($candidate.Path) -> $stash" -ForegroundColor Yellow
            $stashes += [pscustomobject]@{ From = $candidate.Path; Stash = $stash;
                                           Hash = $hash; OneFile = $candidate.OneFile }
        }
    }

    Write-Host "`n== Nettoyage ==" -ForegroundColor Cyan
    Remove-Item -Recurse -Force build\SendToConsole, dist -ErrorAction SilentlyContinue

    Write-Host "`n== Construction ==" -ForegroundColor Cyan
    & $Py @PyArgs -m PyInstaller build\SendToConsole.spec --noconfirm `
        --workpath build\SendToConsole --distpath dist

    if ($OneFile) {
        $exe = Join-Path (Get-Location) "dist\SendToConsole.exe"
        $appDir = Join-Path (Get-Location) "dist"
    } else {
        $exe = Join-Path (Get-Location) "dist\SendToConsole\SendToConsole.exe"
        $appDir = Join-Path (Get-Location) "dist\SendToConsole"
    }

    if (-not (Test-Path $exe)) {
        Write-Host "`nEchec : l'executable n'a pas ete produit." -ForegroundColor Red
        exit 1
    }

    # --- Restitution des entrees chiffrees ----------------------------------
    # Uniquement avec -KeepVault (usage personnel). Sinon dist\ reste livrable
    # sans coffre, et la copie de securite est conservee dans .vault-stash\.
    # On restitue le coffre du mode construit (a defaut, l'unique present) ;
    # les autres copies restent dans .vault-stash\.
    $restore = $null
    if ($KeepVault -and $stashes.Count -gt 0) {
        $restore = $stashes | Where-Object { $_.OneFile -eq [bool]$OneFile } | Select-Object -First 1
        if (-not $restore -and $stashes.Count -eq 1) { $restore = $stashes[0] }
    }
    if ($restore) {
        $dest = Join-Path $appDir "entries.enc"
        Copy-Item $restore.Stash $dest -Force
        Protect-VaultPath $dest
        # La copie de securite n'est supprimee qu'apres verification de la
        # restitution : sinon elle est conservee.
        if ((Get-FileHash $dest).Hash -eq $restore.Hash) {
            Remove-Item $restore.Stash -Force -ErrorAction SilentlyContinue
            Write-Host "Entrees chiffrees restituees : $dest" -ForegroundColor Yellow
        } else {
            Write-Host "Restitution non conforme : copie de securite CONSERVEE ($($restore.Stash))." -ForegroundColor Red
        }
        Write-Host "  (ancien emplacement : $($restore.From))" -ForegroundColor DarkYellow
        Write-Host "  ATTENTION : ne distribuez PAS ce dossier (il contient votre coffre)." -ForegroundColor Red
    } elseif ($stashes.Count -gt 0) {
        Write-Host "Entrees chiffrees NON remises dans dist\ (dossier livrable)." -ForegroundColor Yellow
        Write-Host "  Utilisez -KeepVault pour usage personnel." -ForegroundColor DarkYellow
    }
    foreach ($s in $stashes) {
        if ($s -ne $restore) {
            Write-Host "  Copie conservee : $($s.Stash)  (depuis $($s.From))" -ForegroundColor DarkYellow
        }
    }
    if (Test-Path ".vault-stash") {
        $kept = @(Get-ChildItem ".vault-stash" -Filter "entries-*.enc" -File)
        if ($kept.Count -gt 0) {
            Write-Host ("  .vault-stash\ contient {0} ancienne(s) copie(s) du coffre : supprimez celles devenues inutiles." -f $kept.Count) -ForegroundColor DarkYellow
        }
    }

    $exeMo = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    $dirMo = [math]::Round(((Get-ChildItem $appDir -Recurse -File |
                             Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    $nb = (Get-ChildItem $appDir -Recurse -File).Count

    Write-Host "`nTermine." -ForegroundColor Green
    Write-Host "  Executable : $exe ($exeMo Mo)"
    Write-Host "  Dossier    : $appDir ($dirMo Mo, $nb fichiers)"
    if (-not $OneFile) {
        Write-Host "  Distribuer le DOSSIER complet, pas seulement le .exe." -ForegroundColor Yellow
        Write-Host "  Le fichier d'entrees chiffrees sera cree dans ce dossier au premier lancement"
        Write-Host "  (ne pas distribuer le dossier apres l'avoir lance depuis dist\)."
    } else {
        Write-Host "  Le fichier d'entrees chiffrees sera cree a cote de l'executable"
        Write-Host "  (ne pas distribuer apres l'avoir lance depuis dist\)."
    }
}
finally {
    Remove-Item Env:\STC_ONEFILE  -ErrorAction SilentlyContinue
    Remove-Item Env:\STC_NO_PRUNE -ErrorAction SilentlyContinue
    Pop-Location
}
