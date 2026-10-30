function Register-GlanceResumeTask([string]$Sid, [string]$AgentPath, [string]$Arguments = '') {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $folder = $service.GetFolder('\')
    $task = $service.NewTask(0)
    $task.RegistrationInfo.Description = 'Resume Glance face protection when Windows wakes'
    $task.Settings.Enabled = $true
    $task.Settings.StartWhenAvailable = $true
    $task.Settings.ExecutionTimeLimit = 'PT0S'
    $task.Settings.DisallowStartIfOnBatteries = $false
    $task.Settings.StopIfGoingOnBatteries = $false
    $task.Settings.MultipleInstances = 2 # TASK_INSTANCES_IGNORE_NEW; the agent mutex is a second guard.
    $task.Principal.UserId = $Sid
    $task.Principal.LogonType = 3 # TASK_LOGON_INTERACTIVE_TOKEN
    $task.Principal.RunLevel = 0 # TASK_RUNLEVEL_LUA
    $action = $task.Actions.Create(0)
    $action.Path = $AgentPath
    $action.Arguments = ($Arguments + ' --resume-after-wake').Trim()
    $trigger = $task.Triggers.Create(0) # TASK_TRIGGER_EVENT
    $trigger.Enabled = $true
    $trigger.Subscription = @'
<QueryList>
  <Query Id="0" Path="System">
    <Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select>
  </Query>
</QueryList>
'@
    $folder.RegisterTaskDefinition('Glance Agent Resume', $task, 6, $Sid, $null, 3, $null) | Out-Null
}

function Remove-GlanceResumeTask {
    try {
        $service = New-Object -ComObject Schedule.Service
        $service.Connect()
        $folder = $service.GetFolder('\')
        try {
            $task = $folder.GetTask('Glance Agent Resume')
            try { $task.Stop(0) } catch { }
            $folder.DeleteTask('Glance Agent Resume', 0)
        } catch {
            # A missing task is already removed.
        }
    } catch {
        # Task Scheduler can be unavailable during uninstall; do not leave the provider registered.
    }
}
