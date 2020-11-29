$(function () {
    const DEBUG = false;
    
    let origlog = console.log;
    console.log = (...args) => {
	if(DEBUG) {
	    origlog(...args);
	}
    }

    let setStatusText = (status, buttonPaused) => {
	if(status.org !== "unknown") {
	    orgInfo = ` (${status.org})`;
	}
	switch(status.state) {
	case "broadcasting":
	case "broadcast":
	case "start":
	    $('#statusText').text(`Broadcasting${orgInfo}`);
	    break;
	case "stop":
	    $('#statusText').text(`Stopped`);
	    break;
	case "pause":
	case "paused":
	    $('#statusText').text(`Broadcast paused ${orgInfo}`);
	    break;
	case "holding":
	    $('#statusText').text(`Broadcast held`);
	    break;	    
	}

	let time = 'unknown';
	$('#statusText2').html('&nbsp;');
	if(status.nextTime !== 0) {
	    let now = Date.now();
	    let msDiff = status.nextTime - now;
	    let sDiff = Math.floor(msDiff/1000);
	    time = `${sDiff} seconds`;
	    
	    if(sDiff > 60*60) {
		let hDiff = Math.round(sDiff/60/60);
		time = `${hDiff} hours`;
	    } else if(sDiff > 60) {
		let mDiff = Math.round(sDiff/60);
		time = `${mDiff} minutes`;
	    }
	
	    switch(status.nextState) {
	    case "start":
		$('#statusText2').text(`${status.nextOrg} starting in ${time}`);
		break;
	    case "stop":
		$('#statusText2').text(`Stopping in ${time}`);
		break;
	    default:
		break;
	    }

	}
    };
    
    let setButtonStatus = (state) => {
	$('#extend').hide();
	if(state.match(/broadcast|start|paused/i)) {
	    $('#extend').show();
	}

	$('#pauseResume').removeClass('resume holding');
        if (state === 'paused' || state === 'holding') {
            $('#pauseResume').addClass('resume');
	    if(state === 'holding') {
		$('#pauseResume').addClass('holding');
	    }
        } else if (state === 'stop') {
            $('#pauseResume').addClass('holding');
	}

    };

    let filterSchedule = (schedule) => {
	schedule = schedule.map((entry) => {
	    entry[0] = entry[0].toLowerCase();
	    entry[1] = Date.parse(entry[1]);
	    entry[2] = entry[2].toLowerCase().split(/[_\s]+/).map((word) => { return (word.charAt(0).toUpperCase() + word.slice(1)); }).join(' '); 
	    
	    return entry;
	}).sort((entry1, entry2) => {
	    return entry1[1] - entry2[1];
	});

	return schedule;
    };
    
    let parseStatus = (data) => {
	let schedule = filterSchedule(data.schedule);

	let time = Date.now();

	let state = "unknown";
	let org = "unknown";
	let nextState = "unknown";
	let nextOrg = "unknown";
	let nextTime = 0;
	
	for(let i=0; i<schedule.length; ++i) {
	    let entry = schedule[i];
	    if(time >= entry[1]) {
		state = entry[0];
		org = entry[2];
	    } else {
		nextState = entry[0];
		nextTime = entry[1];
		nextOrg = entry[2];
		if(state === "unknown") {
		    switch(entry[0]) {
		    case "start":
		    case "broadcast":
			state = "stop";
			break;
		    case "stop":
			state = "start";
			break;
		    default:
			state = "unknown";
			break;
		    }
		}
		break;
	    }
	}
	// before first time
	// during something
	// after last time

	let status = {
	    state,
	    org,
	    nextState,
	    nextTime,
	    nextOrg
	};
	
	return status;
    };

    let ajaxAction = (action) => {
	console.log(`ACTION ${action}`);

	$.ajax(`control.php?action=${action}`)
            .done(function (data) {
		console.log(data);
		let buttonPaused = data.buttonPaused;
		
		let status = parseStatus(data);
		console.log(buttonPaused);
		if(buttonPaused) {
		    if (status.state.match(/broadcast|start|pause/i)) {
			status.state = "paused";
		    } else {
			status.state = "holding";
		    }
		}
		console.log(status);
		
		setButtonStatus(status.state);

		setStatusText(status, buttonPaused);
            })
            .fail(function (jqxhr, err) {
		console.log(`error ${err}`);
            });
    };

    $('html').on('click', '#pauseResume', function () {
        let action = 'pause';

        if ($(this).hasClass('resume')) {
            action = 'resume';
        } else if ($(this).hasClass('disabled')) {
	    action = 'status';
	}

	ajaxAction(action);
    });

    $('html').on('click', '#extend', function () {
        let action = 'extend';

	ajaxAction(action);
    });

    ajaxAction('status');
    let statusPoller = setInterval(function() {
	ajaxAction('status');
    }, 1000);
});
