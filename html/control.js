$(function () {
    const DEBUG = true;
    
    let origlog = console.log;
    console.log = (text) => {
	if(DEBUG) {
	    origlog(text);
	}
    }

    let setButtonStatus = (state) => {
	$('#pauseResume').removeClass('resume disabled');
        if (state === 'paused') {
            $('#pauseResume').addClass('resume');
        } else if (state === 'stop') {
            $('#pauseResume').addClass('disabled');
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
	
	for(let i=0; i<schedule.length; ++i) {
	    let entry = schedule[i];
	    if(time >= entry[1]) {
		state = entry[0];
		org = entry[2];
	    } else {
		nextOrg = entry[2];
		nextState = entry[0];
		if(state === "unknown") {
		    switch(entry[0]) {
		    case "start":
		    case "broadcast":
			state = "stop";
			break;
		    case "stop":
		    case "pause":
			state = "start";
			break;
		    default:
			state = "unknown";
			break;
		    }
		    
		    break;
		}
	    }
	}
	// before first time
	// during something
	// after last time

	let status = {
	    state: state,
	    org: org,
	    nextState,
	    nextOrg: nextOrg
	};
	
	return status;
    };

    let ajaxAction = (action) => {
	$.ajax(`control.php?action=${action}`)
            .done(function (data) {
		console.log(data);
		let buttonPaused = data.buttonPaused;
		
		let status = parseStatus(data);
		
		if(buttonPaused && data.state === "broadcasting" || data.state === "start") {
		    status.state = "paused";
		}
		console.log(status);
		
		setButtonStatus(status.state);
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
        console.log(`ACTION ${action}`);

	ajaxAction(action);
        //$.ajax(`control.php?action=${action}`)
        //    .done(function (data) {
        //        console.log(data);
        //    })
        //    .fail(function (jqxhr, err) {
        //        console.log(`error ${err}`);
        //    });
    });

    ajaxAction('status');
    let statusPoller = setInterval(function() {
	ajaxAction('status');
    }, 500);
});
