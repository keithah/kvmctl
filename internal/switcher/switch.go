// Package switcher plans and executes sequential HID switch protocols.
package switcher

import (
	"fmt"
	"time"
)

type Event struct {
	Key   string `json:"key"`
	State string `json:"state"`
}
type Profile struct {
	Name                       string
	MinPort, MaxPort           int
	Sequence                   [][2]string
	InterKeyDelay, SettleDelay time.Duration
}

var TH413 = Profile{Name: "terived-th41-3", MinPort: 1, MaxPort: 4, Sequence: [][2]string{{"ControlRight", "tap"}, {"ControlRight", "tap"}, {"Digit{port}", "tap"}, {"Enter", "tap"}}, InterKeyDelay: 200 * time.Millisecond, SettleDelay: time.Second}

func Plan(p Profile, port int) ([]Event, error) {
	if port < p.MinPort || port > p.MaxPort {
		return nil, fmt.Errorf("port %d out of range %d-%d for profile %s", port, p.MinPort, p.MaxPort, p.Name)
	}
	out := []Event{}
	for _, step := range p.Sequence {
		key := step[0]
		for i := 0; i+len("{port}") <= len(key); i++ {
			if key[i:i+len("{port}")] == "{port}" {
				key = key[:i] + fmt.Sprint(port) + key[i+len("{port}"):]
				break
			}
		}
		switch step[1] {
		case "tap":
			out = append(out, Event{key, "down"}, Event{key, "up"})
		case "press":
			out = append(out, Event{key, "down"})
		case "release":
			out = append(out, Event{key, "up"})
		default:
			return nil, fmt.Errorf("unknown action %q", step[1])
		}
	}
	return out, nil
}
