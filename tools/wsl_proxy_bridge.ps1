param(
  [int]$ListenPort = 17890,
  [string]$TargetHost = "127.0.0.1",
  [int]$TargetPort = 7890
)

$ErrorActionPreference = "Stop"
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $ListenPort)
$listener.Server.SetSocketOption([System.Net.Sockets.SocketOptionLevel]::Socket, [System.Net.Sockets.SocketOptionName]::ReuseAddress, $true)
$listener.Start()
Write-Output "proxy-bridge listening on 0.0.0.0:${ListenPort} -> ${TargetHost}:${TargetPort}"

while ($true) {
  $client = $listener.AcceptTcpClient()
  [void][System.Threading.Tasks.Task]::Run([Action]{
    try {
      $up = [System.Net.Sockets.TcpClient]::new()
      $up.Connect($TargetHost, $TargetPort)

      $cs = $client.GetStream()
      $us = $up.GetStream()

      $t1 = $cs.CopyToAsync($us)
      $t2 = $us.CopyToAsync($cs)
      [void][System.Threading.Tasks.Task]::WaitAny(@($t1,$t2))
    } catch {
    } finally {
      try { $client.Close() } catch {}
      try { if ($up) { $up.Close() } } catch {}
    }
  })
}
