#!/bin/zsh
echo "Vanilla utilities script started!"

seconds=0
autorestart_interval=240
restart_interval=14400
backup_interval=3600
main_screen_name="Vanill"

while true; do
  sleep 1s
  (( seconds++ ))

  # autorestart script
  if (( seconds % autorestart_interval == 0 )); then
    if ! screen -ls | grep -q $main_screen_name; then
      echo "Server was dead, restarting!"
      screen -dmS "Vanilla" java -Xmx4096M -Xms4096M -jar server.jar nogui
    else
      echo "Server is still running!"
    fi
  fi

  # backup script
  if (( seconds % backup_interval == 0 )); then
    found_player=1
    backup_file=$(ls -1t backups/*.tar | head -n 1)
    backup_timestamp=$(basename "$backup_file" .tar | sed 's/_/ /;s/-/:/3' | sed 's/-/:/3')

    backup_epoch=$(date -d "$backup_timestamp" +"%s")
    log_file="logs/latest.log"

    while read -r log_line; do
      log_timestamp=$(echo "$log_line" | grep -o "\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\]" | tr -d '[]')
      if [ "$log_timestamp" ]; then
        log_epoch=$(date -d "$log_timestamp today" +"%s")
        if [ "$log_epoch" -gt "$backup_epoch" ] && echo "$log_line" | grep -q "joined the game"; then
          found_player=0 # player joined since last backup
	  break
        fi
      fi
    done < <(tail -n 500 "$log_file")

    if screen -ls | grep -q $main_screen_name; then
      if [ "$found_player" -eq 0 ]; then
        DATE_FORMAT="%F_%H-%M-%S"
        TIMESTAMP=$(date +$DATE_FORMAT)
        echo "Starting backup"
        screen -r $main_screen_name -X stuff "say Backup goes brr.....$(printf '\r')"
        local BACKUPS=("backups"/*)
        if [[ ${#BACKUPS[@]} -gt 48 ]]; then
          rm "backups/$(basename "${BACKUPS[1]}")"
        fi
        screen -r $main_screen_name -X stuff "save-off$(printf '\r')"
        tar -cf "backups/$TIMESTAMP.tar" "world"
        screen -r $main_screen_name -X stuff "save-on$(printf '\r')"
        screen -r $main_screen_name -X stuff "save-all$(printf '\r')"
        screen -r $main_screen_name -X stuff "say A backup ilyen lett: jó$(printf '\r')"
        echo "Backup done"
      else
        echo "No players were online since the last backup, aborting backup!"
      fi
    else
      echo "Server is dead, aborting backup!"
    fi
  fi
  
  # restart script
  if (( seconds % restart_interval == 0 )); then
    if screen -ls | grep -q $main_screen_name; then
      for i in {0..302}; do
        sleep 1s
        case $i in
 	  0) echo "Starting restart" ;;
	  1) screen -r $main_screen_name -X stuff "say Server is restarting in 5 minutes to avoid lag! $(printf '\r')" ;;
	  240) screen -r $main_screen_name -X stuff "say Server is restarting in 1 minute! $(printf '\r')" ;;
	  270) screen -r $main_screen_name -X stuff "say Server is restarting in 30 seconds! $(printf '\r')" ;;
	  290) screen -r $main_screen_name -X stuff "say Ending the game in 10 seconds! $(printf '\r')" ;;
	  291) screen -r $main_screen_name -X stuff "say Server is restarting in 9 seconds! $(printf '\r')" ;;
	  292) screen -r $main_screen_name -X stuff "say Deleting the dimension in 8 seconds! $(printf '\r')" ;;
	  293) screen -r $main_screen_name -X stuff "say Server is restarting in 7 seconds! $(printf '\r')" ;;
	  294) screen -r $main_screen_name -X stuff "say Worldediting the base in 6 seconds! $(printf '\r')" ;;
	  295) screen -r $main_screen_name -X stuff "say Server is restarting in 5 seconds! $(printf '\r')" ;;
	  296) screen -r $main_screen_name -X stuff "say Deleting everyone's inventory in 4 seconds! $(printf '\r')" ;;
	  297) screen -r $main_screen_name -X stuff "say Server is restarting in 3 seconds! $(printf '\r')" ;;
	  298) screen -r $main_screen_name -X stuff "say Server is restarting in 2 seconds! $(printf '\r')" ;;
	  299) screen -r $main_screen_name -X stuff "say Server is restarting in 1 second! $(printf '\r')" ;;
	  300) screen -r $main_screen_name -X stuff "say Closing server...$(printf '\r')" ;;
	  301) screen -r $main_screen_name -X stuff "stop$(printf '\r')" ;;
	  302) echo "Restart done" ;;
	    *) ;;
	esac
	seconds=0
      done
    else
      echo "Server is dead, aborting restart!"
    fi
  fi
done
