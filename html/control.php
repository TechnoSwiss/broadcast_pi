<?php 

$path = "status";
$action = $_GET["action"];

$data = array("action"=>$action);

if(preg_match("/resume/i", $action)) {
    if(file_exists("$path/pause")) {                                                     
        shell_exec("rm $path/pause");                                                      
    }
} else if(preg_match("/pause/i", $action)) {
    if(!file_exists("$path/pause")) {
        if(!is_dir($path)) {
            mkdir($path);
        }
        touch("$path/pause");
    }
} else if(preg_match("/extend/i", $action)) {
    if(!file_exists("$path/extend")) {
        if(!is_dir($path)) {
            mkdir($path);
        }
        touch("$path/extend");
    }
}

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

$data["buttonPaused"] = $buttonPaused;
$data["schedule"] = $schedule;

header('Content-Type: application/json');

echo json_encode($data);

?>
