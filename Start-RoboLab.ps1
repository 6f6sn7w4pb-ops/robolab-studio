param(
    [switch]$SelfTest, [switch]$Doctor,
    [string]$Distro = 'Ubuntu-22.04',
    [string]$User = '',
    [string]$ProjectPath = '',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$LaunchOptions
)
if ($env:ROBOLAB_WSL_DISTRO) { $Distro = $env:ROBOLAB_WSL_DISTRO }
if ($env:ROBOLAB_WSL_USER -and -not $User) { $User = $env:ROBOLAB_WSL_USER }
if (-not $ProjectPath) { $ProjectPath = $env:ROBOLAB_WSL_PROJECT }
if (-not $ProjectPath) { $ProjectPath = 'AUTO' }
$launchArgs = @('-d', $Distro)
if ($User) { $launchArgs += @('-u', $User) }
$launchArgs += @('--', 'bash', '-s', '--', $ProjectPath)
if ($SelfTest) { $launchArgs += '--self-test' }
if ($Doctor) { $launchArgs += '--doctor' }
if ($LaunchOptions) { $launchArgs += $LaunchOptions }
@'
set -eu
project="$1"
shift
if [ "$project" = AUTO ]; then project="$HOME/projects/robolab-studio"; fi
if [ ! -f "$project/scripts/launch.sh" ]; then
  echo "RoboLab not found: $project. Set ROBOLAB_WSL_PROJECT to the Linux clone path." >&2
  exit 1
fi
exec bash "$project/scripts/launch.sh" "$@";
'@ | & wsl.exe @launchArgs
exit $LASTEXITCODE
