<?php

$path = "status";
$action = $_GET["action"];

$data = array("action"=>$action);

if(!is_dir($path)) {
    mkdir($path);
}

if(preg_match("/resume/i", $action)) {
    if(file_exists("$path/pause")) {
        shell_exec("rm $path/pause");
    }
} else if(preg_match("/pause/i", $action)) {
    if(!file_exists("$path/pause")) {
        touch("$path/pause");
    }
} else if(preg_match("/extend/i", $action)) {
    if(!file_exists("$path/extend")) {
        touch("$path/extend");
    }
} else if(preg_match("/bandwidth(\d+)/i", $action, $matches)) {
    $bandwidth = $matches[1];

    $bwfile = fopen("$path/bandwidth", "w");
    fwrite($bwfile, "$bandwidth\n");
} else if(preg_match("/^preview/i", $action)) {
    if(!file_exists("$path/webstream")) {
        touch("$path/webstream");
    }
} else if(preg_match("/stoppreview/i", $action)) {
    if(file_exists("$path/webstream")) {
        shell_exec("rm $path/webstream");
    }
}  else if(preg_match("/stoprecord/i", $action)) {
    if(file_exists("$path/audio_record")) {
        shell_exec("rm $path/audio_record");
    }
} else if(preg_match("/^record/i", $action)) {
    if(!file_exists("$path/audio_record")) {
        touch("$path/audio_record");
    }
} else if(preg_match("/moving/i", $action)) {
    #$presetfile = fopen("$path/preset", "w");
    #fwrite($presetfile, "0\n");
}

$viewers = 0;
$viewerfile = fopen("$path/watching", "r") or die("Unable to open viewer file!");
while(!feof($viewerfile))
{
    $watching = trim(fgets($viewerfile));
    if(preg_match('/^[1-9][0-9]{0,15}$/',$watching)) {
            $viewers = $watching;
    }
}
fclose($viewerfile);

$schedule = [];
$statusfile = fopen("$path/status", "r") or die("Unable to open status file!");

while(!feof($statusfile))
{
    $line = rtrim(fgets($statusfile));
    if(preg_match('/,/', $line)) {
    array_push($schedule, explode(",", $line));
    }
}
fclose($statusfile);


$buttonPaused = file_exists("$path/pause");

$bandwidth = "0";
if(file_exists("$path/bandwidth")) {
    $bwcontent = file_get_contents("$path/bandwidth");

    $bandwidth = preg_replace("/(\d+)[.\n]*/", "$1", $bwcontent);
}

$preset = "";
if(file_exists("$path/preset")) {
    $prsetcontent = file_get_contents("$path/preset");

    $preset = preg_replace("/(\-?\d+)[.\n]*/", "$1", $prsetcontent);
}

$recording = file_exists("$path/audio_record");

$data["buttonPaused"] = $buttonPaused;
$data["recording"] = $recording;
$data["viewers"] = $viewers;
$data["schedule"] = $schedule;
$data["bandwidth"] = $bandwidth;
$data["preset"] = $preset;

header('Content-Type: application/json');

echo json_encode($data);

?>
