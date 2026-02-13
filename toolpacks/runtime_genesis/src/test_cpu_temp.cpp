#include <iostream>
#include <fstream>
int main() {
    std::ifstream f("/sys/class/thermal/thermal_zone0/temp");
    int t; f >> t;
    std::cout << "CPU temp: " << t/1000.0 << " C" << std::endl;
    return 0;
}
