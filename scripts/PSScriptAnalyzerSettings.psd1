@{
  # Interactive console scripts intentionally use Write-Host; variables in _common.ps1 are used by the scripts
  # that dot-source it (the analyzer checks files in isolation).
  ExcludeRules = @('PSAvoidUsingWriteHost', 'PSUseDeclaredVarsMoreThanAssignments')
}
