$(function () {
    const DEBUG = false;
    
    let origlog = console.log;
    console.log = (...args) => {
	if(DEBUG) {
	    origlog(...args);
	}
    }

    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul','Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    $(this).scrollTop(0);
    $('html').animate({scrollTop:0}, 1);
    $('body').animate({scrollTop:0}, 1);
    
    let formatDate = (timestamp) => {
	let date = new Date(timestamp);

	let ampm = 'am';
	let dow = dayNames[date.getDay()];
	let day = date.getDate();
	let mon = monthNames[date.getMonth()];
	let year = date.getFullYear();
	let hour = date.getHours();
	if(hour > 12) {
	    hour -= 12;
	    ampm = 'pm';
	}
	let min = ('0'+(date.getMinutes())).slice(-2);
	return ` on ${dow}, ${mon} ${day} at ${hour}:${min}${ampm}`;
    };

    let setStatusText = (status) => {
	if(status === undefined) {
	    return;
	}

	let orgInfo = '';
	if(status.org !== undefined && status.org !== 'unknown') {
	    orgInfo = ` (${status.org})`;
	}
	switch(status.state) {
	case 'broadcast':
	case 'start':
	    $('#statusText').text(`Broadcasting${orgInfo}`);
	    break;
	case 'stop':
	    $('#statusText').text(`Stopped`);
	    break;
	case 'pause':
	case 'paused':
	    $('#statusText').text(`Broadcast paused${orgInfo}`);
	    break;
	case 'holding':
	    $('#statusText').text(`Broadcast held`);
	    break;	    
	}

	let time = 'unknown';
	$('#statusText2').html('&nbsp;');
	if(status.nextTime !== 0) {
	    let now = Date.now();
	    let msDiff = status.nextTime - now;
	    let sDiff = Math.floor(msDiff/1000);
	    time = ` in ${sDiff} seconds`;

	    if(sDiff > 24*60*60) {
		time = formatDate(status.nextTime);
	    } else if(sDiff > 60*60) {
		let hDiff = Math.round(sDiff/60/60);
		time = ` in ${hDiff} hours`;
	    } else if(sDiff > 60) {
		let mDiff = Math.round(sDiff/60);
		time = ` in ${mDiff} minutes`;
	    }
	
	    switch(status.nextState) {
	    case 'start':
		$('#statusText2').text(`${status.nextOrg} starting${time}`);
		break;
	    case 'stop':
		$('#statusText2').text(`Stopping${time}`);
		break;
	    default:
		break;
	    }

	}
    };
    
    let setButtonStatus = (state) => {
	if(state === undefined) {
	    return;
	}

	$('#extend').hide();
	if(state.match(/broadcast|start|pause/i)) {
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

    let setBandwidth = (bandwidth) => {
	if(bandwidth !== undefined) {
	    $('.bandwidth').removeClass('selected');
	    $(`.bandwidth[bandwidth=${bandwidth}]`).addClass('selected');
	}
    };

    let setPreset = (preset) => {
	preset = parseInt(preset);

	if(preset === NaN || preset < 0) { // undefined
	    $('.preset').removeClass('selected');
	    return;
	}
	// moving
	if(moving || preset === 0) {
	    return;
	}

	$('.preset').removeClass('selected');
	$(`.preset[preset=${preset}]`).addClass('selected');
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

	let state = 'unknown';
	let org = 'unknown';
	let nextState = 'unknown';
	let nextOrg = 'unknown';
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
		if(state === 'unknown') {
		    switch(entry[0]) {
		    case 'start':
		    case 'broadcast':
			state = 'stop';
			break;
		    case 'stop':
			state = 'start';
			break;
		    default:
			state = 'unknown';
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

                let bandwidth = data.bandwidth;

		let preset = data.preset;

		console.log(buttonPaused);
		if(buttonPaused) {
		    if (status.state.match(/broadcast|start|pause/i)) {
			status.state = 'paused';
		    } else {
			status.state = 'holding';
		    }
		}
		console.log(status);
		
		setButtonStatus(status.state);

		setStatusText(status);

		setBandwidth(bandwidth);

		setPreset(preset);
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

    let moving = false;
    let movingTO = undefined;
    $('html').on('click', '.preset', function () {
	$('.preset').removeClass('selected');
	$(this).addClass('selected');
        let preset = $(this).attr('preset');

	//ajaxAction('moving');
	moving = true;
	clearTimeout(movingTO);
	movingTO = setTimeout(function() {
	    moving = false;
	}, 1000);
        $.ajax(`http://` + window.location.hostname + `:8080/cgi-bin/ptzctrl.cgi?ptzcmd&poscall&${preset}`)
            .done(function (data) {
                console.log(data);
            })
            .fail(function (jqxhr, err) {
                console.log(`error ${err}`);
            });
        
    });

    let cameraInt;
    $('html').on('click', '#previewbutton', function () {
        $('#content').toggleClass('previewopen');
	if($('#content').hasClass('previewopen')) {
	    $('#previewbutton').text('Close');
	    cameraInt = setInterval(() => {
		$('#preview').attr('src', `camera.jpg?${Date.now()}`);
	    }, 333);
	} else {
	    $('#previewbutton').text('Preview');
	    clearInterval(cameraInt);
	    cameraInt = undefined;
	}
    });

    $('html').on('click', '.rehome', function () {
        $.ajax(`http://` + window.location.hostname + `:8080/cgi-bin/param.cgi?pan_tiltdrive_reset`)
            .done(function (data) {
                console.log(data);
            })
            .fail(function (jqxhr, err) {
                console.log(`error ${err}`);
            });

    });

    $('html').on('click', '.bandwidth', function () {
        let bandwidth = $(this).attr('bandwidth');
	ajaxAction(`bandwidth${bandwidth}`);
    });

    $('html').on('click', '.dropdown .label', function () {
        $(this).parent().toggleClass('open');
    });

    let prevStream = undefined;
    ajaxAction('status');
    let statusPoller = setInterval(function() {
	if(cameraInt) {
	    ajaxAction('preview');
	    prevStream = cameraInt;
	} else if(prevStream) {
	    ajaxAction('stoppreview');
	    prevStream = undefined;
	} else {
	    ajaxAction('status');
	}
    }, 1000);

    $(window).on('beforeunload', function() {
	ajaxAction('stoppreview');
    });
});
