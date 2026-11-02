$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json

if ($request.operation -eq 'network') {
    $networkType = [type]::GetTypeFromCLSID([guid]'DCB00C01-570F-4A9B-8D69-199FDBA5723B')
    $network = [Activator]::CreateInstance($networkType)
    @{ ready = [bool]$network.IsConnectedToInternet } | ConvertTo-Json -Compress
    exit 0
}

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$folder = $service.GetFolder('\')
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = $request.name

function Get-Task {
    try { return $folder.GetTask($taskName) }
    catch {
        if ($_.Exception.HResult -eq -2147024894) { return $null }
        throw
    }
}

function Get-Sid($identity) {
    if ($identity -match '^S-1-') { return $identity }
    $account = New-Object System.Security.Principal.NTAccount($identity)
    return $account.Translate([Security.Principal.SecurityIdentifier]).Value
}

function Test-Owned($task) {
    try {
        return ($task.Definition.RegistrationInfo.Source -ceq $request.owner -and
                (Get-Sid $task.Definition.Principal.UserId) -eq $sid)
    } catch { return $false }
}

function Test-Configuration($task) {
    $d = $task.Definition
    $s = $d.Settings
    return ($d.Actions.Count -eq 1 -and $d.Actions.Item(1).Type -eq 0 -and
        $d.Actions.Item(1).Path -ceq $request.python -and
        $d.Actions.Item(1).Arguments -ceq $request.arguments -and
        $d.Actions.Item(1).WorkingDirectory -ceq $request.working_directory -and
        $d.Triggers.Count -eq 1 -and $d.Triggers.Item(1).Type -eq 9 -and
        (Get-Sid $d.Triggers.Item(1).UserId) -eq $sid -and $d.Triggers.Item(1).Enabled -and
        $d.Principal.LogonType -eq 3 -and $d.Principal.RunLevel -eq 0 -and
        $s.MultipleInstances -eq 2 -and -not $s.DisallowStartIfOnBatteries -and
        -not $s.StopIfGoingOnBatteries -and $s.ExecutionTimeLimit -eq 'PT0S' -and
        $s.RestartCount -eq 3 -and $s.RestartInterval -eq 'PT1M' -and
        -not $s.WakeToRun -and -not $s.RunOnlyIfNetworkAvailable -and
        -not $s.RunOnlyIfIdle -and $s.Enabled)
}

$task = Get-Task
if ($request.operation -eq 'install') {
    if ($null -ne $task) {
        if (-not (Test-Owned $task) -or -not (Test-Configuration $task)) {
            throw 'Conflicting startup task; no changes made. Uninstall an owned task before changing its configuration.'
        }
    } else {
        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Source = $request.owner
        $attempt = 'urn:laptopscheduler:' + [guid]::NewGuid().ToString()
        $description = 'Starts LaptopScheduler at current-user logon. Task state does not prove worker health. ' + $attempt
        $definition.RegistrationInfo.Description = $description
        $definition.Principal.UserId = $sid
        $definition.Principal.LogonType = 3
        $definition.Principal.RunLevel = 0
        $trigger = $definition.Triggers.Create(9)
        $trigger.UserId = $sid
        $trigger.Enabled = $true
        $action = $definition.Actions.Create(0)
        $action.Path = $request.python
        $action.Arguments = $request.arguments
        $action.WorkingDirectory = $request.working_directory
        $settings = $definition.Settings
        $settings.MultipleInstances = 2
        $settings.DisallowStartIfOnBatteries = $false
        $settings.StopIfGoingOnBatteries = $false
        $settings.ExecutionTimeLimit = 'PT0S'
        $settings.RestartCount = 3
        $settings.RestartInterval = 'PT1M'
        $settings.WakeToRun = $false
        $settings.RunOnlyIfNetworkAvailable = $false
        $settings.RunOnlyIfIdle = $false
        $settings.Enabled = $true
        $created = $false
        try {
            $null = $folder.RegisterTaskDefinition($taskName, $definition, 2, $sid, $null, 3)
            $created = $true
            $task = Get-Task
            if ($null -eq $task -or -not (Test-Owned $task) -or -not (Test-Configuration $task)) {
                throw 'Startup registration verification failed'
            }
        } catch {
            if ($created) {
                $current = Get-Task
                if ($null -ne $current -and (Test-Owned $current) -and
                    $current.Definition.RegistrationInfo.Description -ceq $description) {
                    $folder.DeleteTask($taskName, 0)
                }
            }
            throw
        }
    }
} elseif ($request.operation -eq 'uninstall') {
    if ($null -ne $task) {
        if (-not (Test-Owned $task)) { throw 'Startup task belongs to another owner; no changes made' }
        $folder.DeleteTask($taskName, 0)
        $task = $null
    }
} elseif ($request.operation -ne 'status') {
    throw 'Unknown operation'
}

if ($null -eq $task) {
    @{ registered = $false; task_name = $taskName } | ConvertTo-Json -Compress
} else {
    $action = $null
    if ($task.Definition.Actions.Count -gt 0 -and $task.Definition.Actions.Item(1).Type -eq 0) {
        $action = $task.Definition.Actions.Item(1)
    }
    @{ registered = $true; task_name = $taskName; owned = [bool](Test-Owned $task)
       python = $action.Path; arguments = $action.Arguments
       working_directory = $action.WorkingDirectory
       state = @('unknown', 'disabled', 'queued', 'ready', 'running')[[int]$task.State]
    } | ConvertTo-Json -Compress
}
