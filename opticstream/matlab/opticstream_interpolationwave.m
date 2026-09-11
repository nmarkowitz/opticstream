function [Wavelengths_l, Wavelengths_r,InterpolatedWavelengths, Ks, Ko1, Ko2]=opticstream_interpolationwave(Parameters)
% [Wavelengths_l, Wavelengths_r,InterpolatedWavelengths,Ks]=interpolationwave_20201130(Parameters)
% Parameters=[PaddingFactor, PaddingLength, OriginalLineLength1, Start1, OriginalLineLength2, Start2]

format long

load(psoct.internal.getDataFile('wave-pixel.mat'));
% mu wavelength wave_prime (where wave_prime == wavelength)

disp(' interpolating buffer ');

if nargin<1
    PaddingFactor=1;
    PaddingLength=PaddingFactor*2048;
    OriginalLineLength1=2048;
    Start1=1;
    OriginalLineLength2=2048;
    Start2=1;
else
    % InterpolationParameters = [PaddingFactor,PaddingLength,OriginalLineLength1,Start1,OriginalLineLength2,Start2];
    PaddingFactor = Parameters(1);
    PaddingLength = Parameters(2);
    OriginalLineLength1 = Parameters(3);
    Start1 = Parameters(4);
    OriginalLineLength2 = Parameters(5);
    Start2 = Parameters(6);
end

% PaddingFactor1=OriginalLineLength1/PaddingLength;
% PaddingFactor2=OriginalLineLength2/PaddingLength;

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% 021309

% position_r=[30 98 169 238 307 377 448 518 589 660 804 876];
% position_l=[18 86 155 224 293 362 432 501 572 641 783 854];
% wave_sample=[805 810 815 820 825 830 835 840 845 850 860 865];
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% 022009
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% position_l=[40 81 150 220 290 361 431 502 573 645 717 784 862 891];
% position_r=[13 54 123 192 261 331 401 471 541 612 682 753 825 853];
% wave_sample=[807 810 815 820 825 830 835 840 845 850 855 860 865 867];

% l is left position pixel recorded, r is right position pixel recorded

% %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% % 071009
% % l is left position pixel recorded, r is right position pixel recorded
% position_l=[2 65.5 144 221 297 372 446 520 592 664 734.5 804 873.5 927.5];
% position_r= [33 93.5 168 242 314 386 457 528 597 666 734 801 868 920]+1024;
% wave_sample=[806 810 815 820 825 830 835 840 845 850 855 860 865 869];
% %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% For CMOS camera
% % 020212
% % l is left position pixel recorded, r is right position pixel recorded
% position_l=[14 98 237 378 517 660 802 944 1088 1231 1374 1520 1663 1807];
% position_r= [2158 2239 2374 2510 2644 2783 2920 3057 3197 3336 3475 3616 3756 3896];
% wave_sample=[802 805 810 815 820 825 830 835 840 845 850 855 860 865];
% %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% 022212
% % horizontal binning disabled
% position_l=[6 22 161 301 442 583 724 866 1007 1153 1293.5 1439 1582 1727.5 1870];
% position_r= [2138 2153 2288 2424 2561 2697 2834 2972 3110 3251 3389 3531 3671 3813 3953]-2048;
% wave_sample=[799.43 800 805 810 815 820 825 830 835 840 845 850 855 860 865];

% horizontal binning enabled, used in CMOS now
% position_l=[3 11 80 150 221 291 362 433 503 576 646.5 719 791 863.5 935];
% position_r= [1069 1076 1144 1212 1280 1348 1417 1486 1555 1625 1694 1765 1835 1906 1976];
% wave_sample=[799.43 800 805 810 815 820 825 830 835 840 845 850 855 860 865];


% 1300 two-cameras
position_l= mu(:,1);
% position_l(1)=position_l(1)-4;
% position_l(2)=position_l(2)+1;
% % position_l(3)=position_l(3)-3;
% % position_l(4)=position_l(4)-2;
% % position_l(5)=position_l(5)-1;
% position_l(6)=position_l(6)-5;
% % position_l(7)=position_l(7)+3;
% % position_l(8)=position_l(8)+4;
% position_l(9)=position_l(9)-6;

position_r= mu(:,2);
% position_r(6)=position_r(6)-6;
% position_r(2)=position_r(2)-6;
% position_r(9)=position_r(9)-8;

wave_sample=wave_prime;
if size(wave_sample)~=size(position_l)
    wave_sample=wave_sample';
end

% position_l=([283 353 616 686 926 999 988 1065 1067 1122 1111 1172 1186 1262 1231 1306 1555 1624])';
% position_r=([282 352 618 687 925 1002 989 1068 1067 1122 1113 1175 1185 1265 1233 1307 1556 1627])';
% wave_sample=([1192.7 1204.3 1246.3 1256 1292 1303 1301.3 1313.2 1312.5 1325.5 1326.5 1338 1333.2 1346.3 1343.5 1355.5 1395.5 1407.3])';
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% Do second order least-squares fitting to find the wavelengths of the whole
% spectrums
pcoeff1=polyfit(position_l,wave_sample,1); % linear interpolation
pcoeff1
x1=1:(Start1+OriginalLineLength1-1);
lamda_l=polyval(pcoeff1,x1);
pcoeff2=polyfit(position_r,wave_sample,1); % linear interpolation
pcoeff2
x2=1:(Start2+OriginalLineLength2-1);
lamda_r=polyval(pcoeff2,x2);

W_l=lamda_l(Start1:Start1+OriginalLineLength1-1);
W_r=lamda_r(Start2:Start2+OriginalLineLength2-1);
xx1=linspace(Start1,Start1+OriginalLineLength1-1,PaddingLength);
Wavelengths_l=1e-9*interp1([Start1:Start1+OriginalLineLength1-1],W_l,xx1,'linear','extrap')';
xx2=linspace(Start2,Start2+OriginalLineLength2-1,PaddingLength);
Wavelengths_r=1e-9*interp1([Start2:Start2+OriginalLineLength2-1],W_r,xx2,'linear','extrap')';
% Wavelengths_l(1)
% Wavelengths_r(1)
% Wavelengths_l(end)
% Wavelengths_r(end)
minK = 2*pi / min([Wavelengths_l(end) Wavelengths_r(end)]);
maxK = 2*pi / max([Wavelengths_l(1) Wavelengths_r(1)]);
% Wavelengths=(Wavelengths_l + Wavelengths_r)/2;
% minK = 2*pi / max(Wavelengths)
% maxK = 2*pi / min(Wavelengths)
Ko1 = (2*pi) ./ Wavelengths_l;
Ko2 = (2*pi) ./ Wavelengths_r;


% figure,plot([lamda_l' lamda_r']); legend('\lambda_l','\lambda_r');
% fout=['/autofs/cluster/octdata2/users/Hui/PSCalibration/SpectrometerCal_10xw_20201016/interpolationwave_lambda_plot.png'];
% print(gcf,fout,'-dpng','-r300');
%
% figure('position',[288 153 960 798]); hold on;
% plot((1:PaddingLength)',[Wavelengths_l Wavelengths_r],'linewidth',1.5);
% plot(position_l*PaddingFactor,1e-9*wave_sample,'.','markersize',14,'color','r');
% plot(position_r*PaddingFactor,1e-9*wave_sample,'.','markersize',14,'color','g');
% legend('wavelengths_l','wavelengths_r','position_l*pad vs wavesample','position_l*pad vs wavesample','Location','best');
% fout=['/autofs/cluster/octdata2/users/Hui/PSCalibration/SpectrometerCal_10xw_20201016/interpolationwave_wavelengths_plot1.png'];
% print(gcf,fout,'-dpng','-r300');
%
% figure,plot((Wavelengths_l-Wavelengths_r)*(1e+9));
% title('wavelengths_l - wavelength_r');
% fout=['/autofs/cluster/octdata2/users/Hui/PSCalibration/SpectrometerCal_10xw_20201016/interpolationwave_wavelengths_plot2.png'];
% print(gcf,fout,'-dpng','-r300');


% %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% clear x1 lamda_l x2 lamda_r W_l W_r xx1 xx2

Ks=flipud(linspace(minK,maxK,PaddingLength)');
InterpolatedWavelengths = (2*pi) ./ Ks;

disp(' ');
