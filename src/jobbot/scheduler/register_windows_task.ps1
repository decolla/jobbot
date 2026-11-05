<#
Registra (ou remove) a tarefa do Agendador de Tarefas do Windows que roda "jobbot run-daily" todo dia.
(Arquivo sem acentos de proposito: o PowerShell 5.1 le .ps1 sem BOM como ANSI.)

Uso, no PowerShell, a partir da pasta do projeto:
  powershell -ExecutionPolicy Bypass -File src\jobbot\scheduler\register_windows_task.ps1
  powershell -ExecutionPolicy Bypass -File src\jobbot\scheduler\register_windows_task.ps1 -Time 19:00
  powershell -ExecutionPolicy Bypass -File src\jobbot\scheduler\register_windows_task.ps1 -Remove

A tarefa roda com o seu usuario e so com voce logado, porque o login do Claude Code e o perfil do
navegador sao da sua conta. Se o computador estiver desligado no horario, ela roda assim que possivel.
A saida de cada execucao vai para data\run-daily.log.
#>
param(
    [string]$Time = "08:30",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "jobbot-run-daily"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarefa $TaskName removida."
    exit 0
}

$Project = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Uv = (Get-Command uv).Source
$Log = Join-Path $Project "data\run-daily.log"
$Command = "cd /d `"$Project`" && `"$Uv`" run jobbot run-daily >> `"$Log`" 2>&1"

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $Command" -WorkingDirectory $Project
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "jobbot: busca vagas, candidata e acompanha processos" -Force | Out-Null

Write-Host "Tarefa $TaskName registrada: todo dia as $Time."
Write-Host "Log em: $Log"
Write-Host "Para testar agora: Start-ScheduledTask -TaskName $TaskName"
