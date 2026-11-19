package org.example.cohort;

import org.apache.commons.lang3.StringUtils;

public final class Greeter {
    private Greeter() {}

    public static String message(String name, boolean formal) {
        String person = StringUtils.defaultIfBlank(name, "cohort").strip();
        if (formal) {
            return "Good day, " + person + ".";
        }
        return "Hello, " + person + "!";
    }

    public static void main(String[] args) {
        String name = System.getProperty("lab.name", "cohort");
        boolean formal = Boolean.getBoolean("lab.formal");
        System.out.println(message(name, formal));
    }
}
