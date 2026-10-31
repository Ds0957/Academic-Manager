; Instalador de Academic Manager para Windows (Inno Setup 6). Se compila con installer\build.ps1,
; que prepara StageDir con el Python portátil (python\) y el código (app\). Ver docs/39.
; Instalación por usuario y sin administrador. Desinstalar nunca toca la carpeta de datos ni
; %APPDATA%\AcademicManager\config.json: solo se borran {app}\python y {app}\app (el asistente no
; acepta una carpeta de datos dentro de {app}), y la tarea diaria solo si apunta a esta copia.

; ArchitecturesAllowed=x64compatible necesita Inno Setup 6.3; Ver es la versión del compilador.
#if Ver < EncodeVer(6, 3, 0)
  #error Hace falta Inno Setup 6.3 o posterior. Use build.ps1 -InstallInnoSetup.
#endif

#define AppName "Academic Manager"
#define VersionHandle FileOpen(AddBackslash(SourcePath) + "..\academic_app\VERSION")
#if !VersionHandle
  #error No se encuentra academic_app\VERSION
#endif
#define AppVersion Trim(FileRead(VersionHandle))
#expr FileClose(VersionHandle)
#ifndef StageDir
  #define StageDir AddBackslash(GetEnv("TEMP")) + "AcademicManager-build\stage"
#endif

[Setup]
AppId={{C2C5A887-9F89-4603-9BC1-E4CD69BC9D30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Academic Manager
DefaultDirName={localappdata}\Programs\AcademicManager
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#StageDir}\..\out
OutputBaseFilename=AcademicManager-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\python\python.exe

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
ConfirmUninstall=¿Quitar %1 de este PC?%n%nSus datos no se borran: la carpeta de datos que eligió en el asistente y su configuración (AppData\Roaming\AcademicManager\config.json) se conservan. Si vuelve a instalar, Academic Manager los encuentra.
UninstalledAll=%1 se quitó de este PC.%n%nSus datos se conservaron: la carpeta de datos y su configuración (AppData\Roaming\AcademicManager\config.json) siguen en su lugar.

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo «{#AppName}» en el escritorio"; GroupDescription: "Accesos directos:"

[InstallDelete]
; El código se reemplaza entero en cada actualización (sin módulos viejos). El Python portátil
; se sobrescribe y conserva WhisperX si ya se había instalado.
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "remove-daily-task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Academic Manager"; Filename: "{app}\python\python.exe"; Parameters: "-m academic_app"; WorkingDir: "{app}\app"; Comment: "Abre el asistente de Academic Manager en el navegador"
Name: "{autodesktop}\Academic Manager"; Filename: "{app}\python\python.exe"; Parameters: "-m academic_app"; WorkingDir: "{app}\app"; Comment: "Abre el asistente de Academic Manager en el navegador"; Tasks: desktopicon

[Run]
Filename: "{app}\python\python.exe"; Parameters: "-m academic_app"; WorkingDir: "{app}\app"; Description: "Abrir {#AppName} ahora"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Quita la tarea AcademicManager-CanvasDaily solo si su acción usa el Python de esta copia.
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\remove-daily-task.ps1"" -Python ""{app}\python\python.exe"""; Flags: runhidden waituntilterminated; RunOnceId: "QuitarTareaDiaria"

[UninstallDelete]
; WhisperX y las cachés de Python que se añadieron después de instalar; nunca los datos.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"
