# VFG voice for Minecraft Java @@=VFG_MC_VERSION@@ -- the installer for Windows.
#
# Built by publish.sh on @@=VFG_BUILT_AT@@. Runs in Windows PowerShell 5.1 and PowerShell 7, from
# vfg-install.cmd or as  irm '<link>' | iex . Safe to run again: it updates the server address, the
# client token and the mod, and leaves everything else as it was. What it does:
#   - adds Fabric Loader @@=VFG_LOADER_VERSION@@ to the launcher's versions (the launcher fetches the rest),
#   - makes its own game folder, .minecraft\instances\vfg-@@=VFG_MC_VERSION@@\, so your other
#     installations and their mods are not touched,
#   - puts Fabric API and the VFG mod in that folder's mods\, each checked by its checksum,
#   - sets the server address and the client token in its config\vfg-client.json, keeping the rest,
#   - adds the server to that folder's server list,
#   - adds a "VFG @@=VFG_MC_VERSION@@" installation to the launcher, backing up its list first.
# Everything runs inside one script block, so nothing is left behind in your PowerShell session,
# and it never calls exit, so pasting it into a window does not close that window.

& {
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ServerAddress = @@VFG_SERVER_ADDRESS@@
$ClientToken = @@VFG_CLIENT_TOKEN@@
$McVersion = @@VFG_MC_VERSION@@
$LoaderVersion = @@VFG_LOADER_VERSION@@
$VfgJarName = @@VFG_JAR_NAME@@
$VfgJarUrl = @@VFG_JAR_URL@@
$VfgJarSha256 = @@VFG_JAR_SHA256@@
$FabricApiName = @@VFG_FABRIC_API_NAME@@
$FabricApiUrl = @@VFG_FABRIC_API_URL@@
$FabricApiSha512 = @@VFG_FABRIC_API_SHA512@@
$ProfileJsonUrl = @@VFG_PROFILE_JSON_URL@@
$ProfileJsonSha256 = @@VFG_PROFILE_JSON_SHA256@@

$VersionId = "fabric-loader-$LoaderVersion-$McVersion"
$ProfileKey = "vfg-$McVersion"
$ProfileName = "VFG $McVersion"
$Instance = "vfg-$McVersion"
$ServerName = 'VFG'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Say([string]$Message) { Write-Host "VFG: $Message" }

function Test-Object($Value) { return ($Value -is [System.Management.Automation.PSCustomObject]) }

function Get-Prop($Object, [string]$Name) {
	$p = $Object.PSObject.Properties[$Name]
	if ($p) { return $p.Value }
	return $null
}

function Set-Prop($Object, [string]$Name, $Value) {
	$Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Read-JsonFile([string]$Path) {
	$text = [System.IO.File]::ReadAllText($Path)
	# PowerShell 7 turns date strings into dates and writes them back changed; 7.5+ can keep them.
	if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {
		return (ConvertFrom-Json -InputObject $text -DateKind String)
	}
	return (ConvertFrom-Json -InputObject $text)
}

function Write-TextFile([string]$Path, [string]$Text) {
	# UTF-8 without a byte-order mark: Windows PowerShell 5.1's own writers add one, which
	# the launcher and the mod do not expect.
	$tmp = "$Path.vfg-tmp"
	[System.IO.File]::WriteAllText($tmp, $Text, $Utf8)
	Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Write-JsonFile([string]$Path, $Object) {
	$json = ConvertTo-Json -InputObject $Object -Depth 64
	$null = ConvertFrom-Json -InputObject $json
	Write-TextFile $Path ($json + "`n")
}

function Copy-Into([string]$Source, [string]$Destination) {
	Copy-Item -LiteralPath $Source -Destination "$Destination.vfg-tmp" -Force
	Move-Item -LiteralPath "$Destination.vfg-tmp" -Destination $Destination -Force
}

# --- servers.dat: uncompressed NBT, a list "servers" of compounds with "name" and "ip" ---

function Read-U16([byte[]]$B, [int]$I) { return (([int]$B[$I] -shl 8) -bor [int]$B[$I + 1]) }

function Read-I32([byte[]]$B, [int]$I) {
	$v = ([long]$B[$I] -shl 24) -bor ([long]$B[$I + 1] -shl 16) -bor ([long]$B[$I + 2] -shl 8) -bor [long]$B[$I + 3]
	if ($v -ge 2147483648) { $v -= 4294967296 }
	if ($v -lt 0) { return 0 }
	return [int]$v
}

# The index just after a payload of NBT tag type $T that starts at $I.
function Skip-Nbt([byte[]]$B, [int]$I, [int]$T) {
	switch ($T) {
		1 { return $I + 1 }
		2 { return $I + 2 }
		3 { return $I + 4 }
		5 { return $I + 4 }
		4 { return $I + 8 }
		6 { return $I + 8 }
		7 { return $I + 4 + (Read-I32 $B $I) }
		8 { return $I + 2 + (Read-U16 $B $I) }
		9 {
			$et = [int]$B[$I]
			$n = Read-I32 $B ($I + 1)
			$I += 5
			for ($k = 0; $k -lt $n; $k++) { $I = Skip-Nbt $B $I $et }
			return $I
		}
		10 {
			while ($true) {
				$tt = [int]$B[$I]
				$I += 1
				if ($tt -eq 0) { return $I }
				$I = Skip-Nbt $B ($I + 2 + (Read-U16 $B $I)) $tt
			}
		}
		11 { return $I + 4 + 4 * (Read-I32 $B $I) }
		12 { return $I + 4 + 8 * (Read-I32 $B $I) }
	}
	throw "unknown NBT tag $T"
}

function Get-EntryName([byte[]]$B, [int]$I) {
	while ($B[$I] -ne 0) {
		$t = [int]$B[$I]
		$n = Read-U16 $B ($I + 1)
		$key = [System.Text.Encoding]::UTF8.GetString($B, $I + 3, $n)
		$j = $I + 3 + $n
		if ($t -eq 8 -and $key -ceq 'name') {
			return [System.Text.Encoding]::UTF8.GetString($B, $j + 2, (Read-U16 $B $j))
		}
		$I = Skip-Nbt $B $j $t
	}
	return $null
}

function Write-Bytes($Stream, [byte[]]$Bytes) { $Stream.Write($Bytes, 0, $Bytes.Length) }

function Write-NbtName($Stream, [string]$Text) {
	$raw = [System.Text.Encoding]::UTF8.GetBytes($Text)
	Write-Bytes $Stream ([byte[]]@((($raw.Length -shr 8) -band 255), ($raw.Length -band 255)))
	Write-Bytes $Stream $raw
}

# Puts {name, ip} first in servers.dat, keeping every other entry and tag.
function Set-ServerEntry([string]$Path, [string]$Name, [string]$Ip) {
	$b = $null
	$headLength = 0
	$others = New-Object System.Collections.ArrayList
	$entries = New-Object System.Collections.ArrayList
	if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 0) {
		$b = [System.IO.File]::ReadAllBytes($Path)
		if ($b[0] -ne 10) { throw 'servers.dat does not start with a compound' }
		$i = 3 + (Read-U16 $b 1)
		$headLength = $i
		while ($b[$i] -ne 0) {
			$t = [int]$b[$i]
			$start = $i
			$n = Read-U16 $b ($i + 1)
			$key = [System.Text.Encoding]::UTF8.GetString($b, $i + 3, $n)
			$j = $i + 3 + $n
			if ($t -eq 9 -and $key -ceq 'servers') {
				$et = [int]$b[$j]
				$count = Read-I32 $b ($j + 1)
				$k = $j + 5
				if ($count -gt 0 -and $et -ne 10) { throw 'servers is not a list of compounds' }
				for ($c = 0; $c -lt $count; $c++) {
					$s = $k
					$k = Skip-Nbt $b $k 10
					if ((Get-EntryName $b $s) -cne $Name) { [void]$entries.Add(@($s, ($k - $s))) }
				}
				$i = $k
			} else {
				$i = Skip-Nbt $b $j $t
				[void]$others.Add(@($start, ($i - $start)))
			}
		}
	}
	$ms = New-Object System.IO.MemoryStream
	if ($null -ne $b) { $ms.Write($b, 0, $headLength) } else { Write-Bytes $ms ([byte[]]@(10, 0, 0)) }
	foreach ($o in $others) { $ms.Write($b, $o[0], $o[1]) }
	Write-Bytes $ms ([byte[]]@(9))
	Write-NbtName $ms 'servers'
	$total = $entries.Count + 1
	Write-Bytes $ms ([byte[]]@(10, (($total -shr 24) -band 255), (($total -shr 16) -band 255), (($total -shr 8) -band 255), ($total -band 255)))
	Write-Bytes $ms ([byte[]]@(8))
	Write-NbtName $ms 'name'
	Write-NbtName $ms $Name
	Write-Bytes $ms ([byte[]]@(8))
	Write-NbtName $ms 'ip'
	Write-NbtName $ms $Ip
	Write-Bytes $ms ([byte[]]@(0))
	foreach ($e in $entries) { $ms.Write($b, $e[0], $e[1]) }
	Write-Bytes $ms ([byte[]]@(0))
	$tmp = "$Path.vfg-tmp"
	[System.IO.File]::WriteAllBytes($tmp, $ms.ToArray())
	Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Test-LauncherRunning {
	$p = @(Get-Process -Name 'MinecraftLauncher', 'Minecraft' -ErrorAction SilentlyContinue)
	return ($p.Count -gt 0)
}

function Get-Download([string]$Url, [string]$File, [string]$Algorithm, [string]$Want, [string]$Label) {
	Say "Downloading $Label"
	$done = $false
	for ($try = 1; $try -le 3 -and -not $done; $try++) {
		try {
			Invoke-WebRequest -Uri $Url -OutFile $File -UseBasicParsing -TimeoutSec 300
			$done = $true
		} catch {
			if ($try -lt 3) { Start-Sleep -Seconds 2 }
		}
	}
	if (-not $done) {
		throw "Could not download $Label. Check your internet connection and run this again. If it keeps failing, the link has probably expired: ask the server owner for today's link."
	}
	$got = (Get-FileHash -LiteralPath $File -Algorithm $Algorithm).Hash.ToLowerInvariant()
	if ($got -ne $Want) {
		throw "$Label failed its checksum: the download was damaged or changed, so nothing was installed. Run this again; if it keeps failing, ask the server owner for a new link."
	}
}

function Install-Vfg([string]$Work) {
	# 1. The Minecraft folder, as the launcher set it up.
	if (-not $env:APPDATA) { throw 'This installer is for Windows. On Linux or a Mac, use vfg-install.sh from the same page.' }
	$game = Join-Path $env:APPDATA '.minecraft'
	$profileFiles = @(
		@('launcher_profiles.json', 'launcher_profiles_microsoft_store.json') |
			ForEach-Object { Join-Path $game $_ } |
			Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
	)
	if ($profileFiles.Count -eq 0) {
		throw "No Minecraft folder with a launcher in it here (looked in $game). Install and run the Minecraft launcher once first (open it and sign in), then run this again."
	}

	# 2. The launcher rewrites its list of installations when it closes: wait for it.
	if (Test-LauncherRunning) {
		Say 'The Minecraft launcher is open. Close it: it rewrites its list of installations when it closes.'
		Say 'Waiting for it to close... (Ctrl+C to stop)'
		while (Test-LauncherRunning) { Start-Sleep -Seconds 2 }
		Say 'Closed. Carrying on.'
	}

	foreach ($pf in $profileFiles) {
		$lp = $null
		try { $lp = Read-JsonFile $pf } catch { $lp = $null }
		if (-not (Test-Object $lp)) {
			throw "$pf is not valid JSON, so it was left alone. Open the Minecraft launcher once (it repairs it), close it, and run this again."
		}
		$profiles = Get-Prop $lp 'profiles'
		if ($null -ne $profiles -and -not (Test-Object $profiles)) {
			throw "$pf does not look like the launcher's list of installations, so it was left alone."
		}
	}

	# 3 and 4. The downloads, each checked before anything is installed.
	try {
		[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
	} catch { }
	$profileTmp = Join-Path $Work 'profile.json'
	$fabricTmp = Join-Path $Work 'fabric-api.jar'
	$vfgTmp = Join-Path $Work 'vfg.jar'
	Get-Download $ProfileJsonUrl $profileTmp 'SHA256' $ProfileJsonSha256 "Fabric Loader $LoaderVersion"
	Get-Download $FabricApiUrl $fabricTmp 'SHA512' $FabricApiSha512 "Fabric API ($FabricApiName)"
	Get-Download $VfgJarUrl $vfgTmp 'SHA256' $VfgJarSha256 "the VFG mod ($VfgJarName)"

	Say "Installing into $game"
	$now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", [System.Globalization.CultureInfo]::InvariantCulture)

	# Fabric Loader, as its own installer writes it: the profile JSON and an empty jar beside it.
	$versionDir = Join-Path (Join-Path $game 'versions') $VersionId
	$null = New-Item -ItemType Directory -Force -Path $versionDir
	Copy-Into $profileTmp (Join-Path $versionDir "$VersionId.json")
	[System.IO.File]::WriteAllBytes((Join-Path $versionDir "$VersionId.jar"), (New-Object byte[] 0))

	# The game folder of its own, and its mods: this VFG and this Fabric API, no older copies.
	$inst = Join-Path (Join-Path $game 'instances') $Instance
	$mods = Join-Path $inst 'mods'
	$configDir = Join-Path $inst 'config'
	$null = New-Item -ItemType Directory -Force -Path $mods
	$null = New-Item -ItemType Directory -Force -Path $configDir
	Get-ChildItem -LiteralPath $mods -File |
		Where-Object { ($_.Name -like 'vfg-client-*.jar' -or $_.Name -like 'fabric-api-*.jar') -and $_.Name -ne $VfgJarName -and $_.Name -ne $FabricApiName } |
		ForEach-Object {
			Remove-Item -LiteralPath $_.FullName -Force
			Say "Removed the older $($_.Name)"
		}
	Copy-Into $fabricTmp (Join-Path $mods $FabricApiName)
	Copy-Into $vfgTmp (Join-Path $mods $VfgJarName)

	# The mod's config: today's address and token; your mic and every other setting stay.
	$cfgPath = Join-Path $configDir 'vfg-client.json'
	$cfg = $null
	if (Test-Path -LiteralPath $cfgPath) {
		try { $cfg = Read-JsonFile $cfgPath } catch { $cfg = $null }
		if (-not (Test-Object $cfg)) {
			Move-Item -LiteralPath $cfgPath -Destination "$cfgPath.bad" -Force
			Say 'The old vfg-client.json did not read as JSON; it is kept as vfg-client.json.bad'
			$cfg = $null
		}
	}
	if ($null -eq $cfg) { $cfg = New-Object PSObject }
	Set-Prop $cfg 'server_address' $ServerAddress
	Set-Prop $cfg 'client_token' $ClientToken
	Write-JsonFile $cfgPath $cfg
	$bridge = Get-Prop $cfg 'bridge_url'
	if ($bridge -is [string] -and $bridge.Trim()) {
		Say "Note: bridge_url in $cfgPath is set to $bridge, so your voice goes there. Leave it empty unless the server owner told you to set it."
	}

	# The server list of that game folder.
	try {
		Set-ServerEntry (Join-Path $inst 'servers.dat') $ServerName $ServerAddress
	} catch {
		Say "Could not add the server to the list. In the game, add it by hand: $ServerAddress"
	}

	# The launcher's installation, after a backup of each list it keeps.
	foreach ($pf in $profileFiles) {
		if (-not (Test-Path -LiteralPath "$pf.before-vfg")) { Copy-Item -LiteralPath $pf -Destination "$pf.before-vfg" }
		Copy-Item -LiteralPath $pf -Destination "$pf.vfg-bak" -Force
		$lp = Read-JsonFile $pf
		$profiles = Get-Prop $lp 'profiles'
		if (-not (Test-Object $profiles)) {
			$profiles = New-Object PSObject
			Set-Prop $lp 'profiles' $profiles
		}
		$p = Get-Prop $profiles $ProfileKey
		if (-not (Test-Object $p)) {
			$p = [pscustomobject]@{ name = $ProfileName; type = 'custom'; created = $now; icon = 'Crafting_Table' }
			Set-Prop $profiles $ProfileKey $p
		}
		Set-Prop $p 'name' $ProfileName
		Set-Prop $p 'type' 'custom'
		Set-Prop $p 'lastVersionId' $VersionId
		Set-Prop $p 'gameDir' $inst
		Set-Prop $p 'lastUsed' $now
		Write-JsonFile $pf $lp
	}

	Write-Host ''
	Write-Host 'VFG: Done. VFG voice is installed.' -ForegroundColor Green
	Write-Host "  1. Open the Minecraft launcher, pick `"$ProfileName`" next to the Play button, and press Play."
	Write-Host "  2. Multiplayer -> `"$ServerName`" ($ServerAddress) -> Join Server."
	Write-Host '  3. Press K in the game to pick your microphone. M mutes.'
	Write-Host '  The first start downloads the speech engine in the background; the mic icon shows its progress.'
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ('vfg-install-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Force -Path $work
# vfg-install.cmd reads $global:VfgInstallOk for its exit code.
$global:VfgInstallOk = $false
try {
	Install-Vfg $work
	$global:VfgInstallOk = $true
} catch {
	Write-Host ''
	Write-Host "VFG: $($_.Exception.Message)" -ForegroundColor Red
} finally {
	Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
}
