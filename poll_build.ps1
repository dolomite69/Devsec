param([string]$Id)
for ($i=0; $i -lt 50; $i++) {
  Start-Sleep -Seconds 6
  try { $res = Invoke-RestMethod -Uri "http://localhost:8000/api/builds/$Id/result" -Method Get } catch { continue }
  if ($res.status -eq "SUCCESS" -or $res.status -eq "FAILED") { break }
}
Write-Output "STATUS=$($res.status)"
Write-Output "==== DOCKERFILE ===="
Write-Output $res.dockerfile
Write-Output "==== LAST LOGS ===="
$res.logs | Select-Object -Last 60 | ForEach-Object { Write-Output $_ }
